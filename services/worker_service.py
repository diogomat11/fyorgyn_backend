from sqlalchemy.orm import Session
from sqlalchemy import cast, Text, or_
from models import Worker, Job, Log
from datetime import datetime, timezone, timedelta
import json
import re

# ── Classificação de falhas OP19/IPASGO ──────────────────────────────────────
# Códigos HTTP só contam com borda de palavra (\b403\b): substring simples
# ("403" in msg) dava falso positivo com dígitos de guia/senha/tamanho.
_RE_BLOQUEIO = re.compile(r"\b(403|429)\b")
_RE_INSTABILIDADE = re.compile(r"\b(401|500|502|503)\b")
_KEYWORDS_BLOQUEIO = ("bloqueio", "bloqueado", "rate limit", "too many requests", "limite de requisições")
_KEYWORDS_INSTABILIDADE = ("sessão rejeitada", "sessao rejeitada", "timeout", "portal indisponível")

def is_bloqueio_ipasgo(msg: str) -> bool:
    """Bloqueio real do portal (403/429/rate-limit): merece fail-fast imediato."""
    if not msg:
        return False
    low = msg.lower()
    return bool(_RE_BLOQUEIO.search(low)) or any(k in low for k in _KEYWORDS_BLOQUEIO)

def is_instabilidade_portal(msg: str) -> bool:
    """Sessão rejeitada/portal instável (401/500/502/503/timeout): também pausa a fila,
    mas não é bloqueio propriamente dito."""
    if not msg:
        return False
    low = msg.lower()
    return any(k in low for k in _KEYWORDS_INSTABILIDADE) or bool(_RE_INSTABILIDADE.search(low))

def e_cascata_op19(msg: str) -> bool:
    """Job abortado em cascata (o anexo NÃO foi tentado): é efeito de outro job,
    não evidência de portal bloqueado. Breakers não devem contar como falha real —
    senão reprocessar 1 anexo re-aborta na hora sem tocar no portal."""
    low = str(msg or "").lower()
    return low.startswith("abortado em cascata") or low.startswith("abortado -") or "não foi tentado" in low

e_cascata_op21 = e_cascata_op19
e_cascata_anexos = e_cascata_op19

def e_erro_infra(msg: str) -> bool:
    """Falha de infraestrutura local (worker offline, conexão recusada): o portal
    nem foi acionado — abortar a fila por causa disso só atrapalha o reprocesso
    enquanto a frota de workers sobe/desce."""
    low = str(msg or "").lower()
    return "worker is offline" in low or "connection refused" in low or "connection error" in low

_PERMANENT_MARKERS = (
    "não encontrado", "nao encontrado", "não é um pdf", "está vazio",
    "limite 5mb", "não informado", "não registrada/ativa no catálogo",
)

def e_erro_permanente(msg: str) -> bool:
    """Erro que nenhum retry resolve (arquivo inexistente/inválido, params faltando):
    fica em erro para reprocesso manual — a auto-recuperação não o reenfileira."""
    low = str(msg or "").lower()
    return any(k in low for k in _PERMANENT_MARKERS)

# Cooldowns de reenvio automático (segundos) — configuráveis por env.
import os as _os
_AUTO_RETRY_INFRA = int(_os.getenv("OP19_AUTO_RETRY_INFRA", "60") or 60)
_AUTO_RETRY_SOFT = int(_os.getenv("OP19_AUTO_RETRY_SOFT", "180") or 180)
_AUTO_RETRY_HARD = int(_os.getenv("OP19_AUTO_RETRY_HARD", "900") or 900)

def _cooldown_auto_retry(msg: str, hard_no_proto: bool) -> int:
    """Cooldown (s) para o reenvio automático, por tipo de falha."""
    if hard_no_proto:
        return _AUTO_RETRY_HARD
    low = str(msg or "").strip()
    if not low or e_erro_infra(low):
        return _AUTO_RETRY_INFRA
    return _AUTO_RETRY_SOFT

def sync_and_abort_anexos_jobs(db: Session, rotinas: list = None, tag_log: str = "OP19", protocolo: str = None) -> bool:
    """
    Garante que lotes IPASGO de anexos (OP19 / OP21) com >= 3 tentativas falhas, erro de bloqueio
    ou jobs travados em 'processing' (> 300s) tenham toda a fila pendente/processing abortada
    e libera os slots de Worker ocupados por jobs já encerrados/abortados.
    """
    if not rotinas:
        rotinas = ["19", "op19_anexar_arquivo_guia"]
    changed = False
    now = datetime.now(timezone.utc)
    try:
        q = db.query(Job).filter(
            Job.id_convenio == 6,
            Job.rotina.in_(rotinas),
            Job.status.in_(["pending", "processing", "running", "error"])
        )
        if protocolo:
            q = q.filter(
                or_(
                    Job.params["protocolo"].astext == protocolo,
                    cast(Job.params, Text).ilike(f'%"{protocolo}"%')
                )
            )
        anexos_jobs = q.all()

        by_proto = {}
        for j in anexos_jobs:
            p_raw = j.params
            try:
                p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
            except Exception:
                p = {}
            proto = str(p.get("protocolo") or p.get("capa") or "_sem_protocolo_").strip()
            by_proto.setdefault(proto, []).append(j)

        aborted_job_ids = set()
        for proto, jobs_list in by_proto.items():
            falhas = 0
            has_block = False
            has_stuck = False
            motivo = "bloqueio"

            for j in jobs_list:
                upd = j.updated_at or j.created_at
                if upd and not upd.tzinfo:
                    upd = upd.replace(tzinfo=timezone.utc)
                if j.status in ("processing", "running") and upd and (now - upd) > timedelta(seconds=300):
                    # Timeout apenas deste job individual travado (não aborta os demais da fila)
                    j.status = "error"
                    j.attempts = max(3, j.attempts or 0)
                    j.error_message = "Timeout: job travado em processing (>300s)."
                    j.locked_by = None
                    changed = True
                elif j.status == "error":
                    msg = str(j.error_message or "")
                    if msg.strip() and not e_cascata_op19(msg) and not e_erro_infra(msg) and not e_erro_permanente(msg):
                        if is_bloqueio_ipasgo(msg):
                            has_block = True
                            motivo = "bloqueio do portal (403/429)"
                        elif (j.attempts or 0) >= 3 and is_instabilidade_portal(msg):
                            has_block = True
                            motivo = "sessão rejeitada/instabilidade repetida no portal"

            if has_block:
                abortadas_neste_proto = 0
                for j in jobs_list:
                    aborted_job_ids.add(j.id)
                    if j.status in ("pending", "processing", "running") or (j.attempts or 0) < 3:
                        j.status = "error"
                        j.attempts = max(3, j.attempts or 0)
                        j.locked_by = None
                        if not j.error_message:
                            j.error_message = (
                                f"Abortado em cascata ({motivo}) — este anexo não foi tentado. "
                                "Reenvio automático após pausa."
                            )
                        j.updated_at = now
                        abortadas_neste_proto += 1
                        changed = True
                if abortadas_neste_proto:
                    try:
                        db.add(Log(
                            job_id=jobs_list[0].id,
                            user_id=jobs_list[0].user_id,
                            level="WARN",
                            message=(f"[{tag_log}] {abortadas_neste_proto} job(s) de anexo do protocolo {proto} "
                                     f"abortados em cascata ({motivo}). Reenvio automático após pausa."),
                        ))
                    except Exception:
                        pass

            # ── Auto-recuperação (circuit breaker com auto-reset) ──────────────
            em_voo = any(j.status in ("pending", "processing", "running") for j in jobs_list)
            if not em_voo:
                hard_no_proto = any(
                    j.status == "error"
                    and not e_cascata_op19(str(j.error_message or ""))
                    and is_bloqueio_ipasgo(str(j.error_message or ""))
                    for j in jobs_list
                )
                reenfileirados = 0
                for j in jobs_list:
                    if j.status != "error":
                        continue
                    msg_j = str(j.error_message or "")
                    if e_erro_permanente(msg_j):
                        continue
                    _m_auto = re.search(r"\[auto-retry (\d+)/5\]", msg_j)
                    n_auto = int(_m_auto.group(1)) if _m_auto else 0
                    if n_auto >= 5:
                        continue
                    upd = j.updated_at or j.created_at
                    if upd and not upd.tzinfo:
                        upd = upd.replace(tzinfo=timezone.utc)
                    idade = (now - upd).total_seconds() if upd else 0
                    cd = _cooldown_auto_retry(msg_j, hard_no_proto)
                    if idade >= cd:
                        j.status = "pending"
                        j.attempts = 0
                        j.locked_by = None
                        j.error_message = f"[auto-retry {n_auto + 1}/5]"
                        j.updated_at = now
                        reenfileirados += 1
                        changed = True
                if reenfileirados:
                    try:
                        db.add(Log(
                            job_id=jobs_list[0].id,
                            user_id=jobs_list[0].user_id,
                            level="INFO",
                            message=(f"[{tag_log}] Auto-recuperação: {reenfileirados} job(s) de anexo do protocolo "
                                     f"{proto} reenviados para a fila após cooldown"
                                     f" ({'bloqueio' if hard_no_proto else 'falha do portal'})."),
                        ))
                    except Exception:
                        pass

        # Reconciliar workers ocupados vinculados aos jobs abortados desta rotina
        if aborted_job_ids:
            busy_workers = db.query(Worker).filter(Worker.current_job_id.in_(aborted_job_ids)).all()
            for w in busy_workers:
                w.command = "restart"
                w.status = "idle"
                w.current_job_id = None
                changed = True

        if changed:
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return changed


def sync_and_abort_op19_jobs(db: Session, protocolo: str = None) -> bool:
    return sync_and_abort_anexos_jobs(db, rotinas=["19", "op19_anexar_arquivo_guia"], tag_log="OP19", protocolo=protocolo)


def sync_and_abort_op21_jobs(db: Session, protocolo: str = None) -> bool:
    return sync_and_abort_anexos_jobs(db, rotinas=["21", "op21_anexar_recurso_glosa"], tag_log="OP21", protocolo=protocolo)


def register_heartbeat(db: Session, hostname: str, status: str, current_job_id: int = None, meta: dict = None):
    """
    Registers a heartbeat from a worker.
    Creates the worker if it doesn't exist.
    Updates status, last_heartbeat, and other fields.
    Returns any pending command for the worker.
    """
    # Normalize hostname to title case for cleaner UI (Sama-9000 instead of sama-9000)
    hostname = hostname.title() if hostname else "Unknown"
    
    # Use ilike to prevent case-sensitive duplicates just in case
    worker = db.query(Worker).filter(Worker.hostname.ilike(hostname)).first()
    
    now = datetime.now(timezone.utc)
    
    if not worker:
        worker = Worker(hostname=hostname)
        db.add(worker)
    
    sync_and_abort_op19_jobs(db)
    if current_job_id and status in ("busy", "processing"):
        jb = db.query(Job.status, Job.rotina).filter(Job.id == current_job_id).first()
        if not jb or jb[0] not in ("processing", "running"):
            status = "idle"
            if jb and str(jb[1]) in ("19", "op19_anexar_arquivo_guia") and jb[0] == "error":
                worker.command = "restart"
            current_job_id = None

    worker.status = status
    worker.last_heartbeat = now
    worker.current_job_id = current_job_id
    if meta:
        worker.meta = json.dumps(meta)

    # Auto-Restart Logic for stuck Error state
    if status == 'error':
        if not worker.first_error_at:
            worker.first_error_at = now
        else:
            # Check duration
            diff = now - worker.first_error_at
            if diff > timedelta(minutes=15):
                worker.command = "restart"
                worker.first_error_at = None # Reset timer after queuing restart
    else:
        # If status is NOT error (idle, processing), clear the error timer
        if worker.first_error_at:
            worker.first_error_at = None
    
    # Check for pending command
    command = worker.command
    
    # If command is 'restart', we send it once and then clear it? 
    # Or strict 'ack' logic? For simplicity, we assume if we send it, it's received.
    # But usually a worker would acknowledge. 
    # Let's keep it simple: if command is present, we return it. 
    # The worker should probably clear it via a separate call or we clear it here?
    # Clearing it here implies "at most once" delivery (if net fails, command lost).
    # Not clearing implies "at least once" (worker might restart loop).
    # Let's clear it here for now as a simple signal.
    
    if worker.command:
        worker.command = None 
        
    db.commit()
    db.refresh(worker)
    
    return {"command": command}

def get_all_workers(db: Session):
    """
    Returns all workers.
    Logic to mark them as 'offline' if heartbeat > 1 min is NOT persisted here to avoid DB writes on GET.
    Frontend or specific cleanup job should handle that visual status.
    """
    sync_and_abort_op19_jobs(db)
    return db.query(Worker).all()

def queue_restart_command(db: Session, worker_id: int):
    """
    Queues a restart command for a specific worker.
    """
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if worker:
        worker.command = "restart"
        db.commit()
        db.refresh(worker)
    return worker
