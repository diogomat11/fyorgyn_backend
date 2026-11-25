
from sqlalchemy.orm import Session
from sqlalchemy import cast, Text
from models import Carteirinha, Job, Worker, Log
from datetime import datetime, timezone, timedelta
import logging
import json
import os

from services import storage_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reclaim de jobs órfãos (incidente 2026-10-23): frota morta deixava jobs em
# 'processing' com lock sem dono — o recover_stuck_jobs do dispatcher só roda
# COM a frota viva (limiar de 180min). O hub é o coordenador (os heartbeats
# dos slots chegam nele), então é ele quem recupera quando a frota some.
RECLAIM_STALE_MINUTES = int(os.environ.get("RECLAIM_STALE_MINUTES", "30") or 30)
RECLAIM_HEARTBEAT_FRESH_MINUTES = int(os.environ.get("RECLAIM_HEARTBEAT_FRESH_MINUTES", "10") or 10)


def _porta_do_slot(locked_by):
    """Extrai a porta do locked_by (ex.: 'http://127.0.0.1:9000' → '9000')."""
    if not isinstance(locked_by, str) or ":" not in locked_by:
        return None
    porta = locked_by.rsplit(":", 1)[-1].strip("/ ")
    return porta if porta.isdigit() else None


def _heartbeat_fresh(heartbeat, now=None, fresh_minutes: int = RECLAIM_HEARTBEAT_FRESH_MINUTES) -> bool:
    """True se o heartbeat do slot é recente (slot presumido vivo). Puro — testável."""
    if heartbeat is None:
        return False
    now = now or datetime.now(timezone.utc)
    hb = heartbeat if heartbeat.tzinfo else heartbeat.replace(tzinfo=timezone.utc)
    return (now - hb) <= timedelta(minutes=fresh_minutes)


def reclaim_orphaned_processing_jobs(db: Session) -> dict:
    """
    Recupera jobs 'processing' órfãos: sem update há > RECLAIM_STALE_MINUTES E
    cujo slot (locked_by → porta → worker.workers.hostname 'Sama-<porta>') não
    heartbeata há > RECLAIM_HEARTBEAT_FRESH_MINUTES. Jobs longos renovam
    updated_at em chunks (OP4 ~2h40m), então staleness do job + heartbeat morto
    é prova suficiente de órfão. Worker vivo → job intocado (o dispatcher
    continua dono do caso 'worker vivo, job lento').
    """
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(minutes=RECLAIM_STALE_MINUTES)
    resumo = {"reclaimed_pending": 0, "reclaimed_error": 0, "skipped_worker_vivo": 0}
    try:
        jobs = db.query(Job).filter(
            Job.status == "processing",
            Job.updated_at < threshold
        ).all()

        for job in jobs:
            porta = _porta_do_slot(job.locked_by)
            slot_vivo = False
            if porta:
                worker = db.query(Worker).filter(
                    Worker.hostname.ilike(f"%-{porta}")
                ).first()
                if worker is not None:
                    slot_vivo = _heartbeat_fresh(worker.last_heartbeat, now)
            if slot_vivo:
                resumo["skipped_worker_vivo"] += 1
                continue

            esgotou = (job.attempts or 0) >= (job.max_attempts or 3)
            slot_antigo = job.locked_by
            job.status = "error" if esgotou else "pending"
            job.locked_by = None
            job.updated_at = now
            if esgotou:
                job.error_message = f"Worker morreu sem finalizar (reclaim do hub: slot {slot_antigo} sem heartbeat)"
                resumo["reclaimed_error"] += 1
            else:
                resumo["reclaimed_pending"] += 1
            db.add(Log(
                job_id=job.id, user_id=job.user_id, level="WARNING",
                message=f"[RECLAIM] Job órfão recuperado pelo hub (slot {slot_antigo} sem heartbeat): -> {job.status}"
            ))

        if resumo["reclaimed_pending"] or resumo["reclaimed_error"]:
            db.commit()
            logger.warning(f"[RECLAIM] {resumo}")
    except Exception as e:
        logger.error(f"Reclaim de jobs órfãos falhou: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    return resumo

def delete_expired_patients(db: Session):
    """
    Deletes temporary patients whose expiration time has passed.
    Cascading deletes should handle related Jobs, Guias, and PEI records.
    """
    try:
        now = datetime.now(timezone.utc)

        expired_patients = db.query(Carteirinha).filter(
            Carteirinha.is_temporary == True,
            Carteirinha.expires_at <= now
        ).all()

        count = len(expired_patients)
        db.commit()  # fecha a transação da leitura — sem 'idle in transaction'
                     # durante o resto do ciclo (Supavisor mata conexão ociosa)
        if count > 0:
            logger.info(f"Cleanup: Found {count} expired temporary patients. Deleting...")

            for patient in expired_patients:
                logger.info(f"Deleting expired patient: {patient.carteirinha} (ID: {patient.id})")
                db.delete(patient)

            db.commit()
            logger.info("Cleanup successfully completed.")
        else:
            logger.debug("Cleanup: No expired patients found.")

        return count

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        db.rollback()
        return 0


def _is_anexo_url(val) -> bool:
    """True se val referencia um anexo (signed URL do Storage ou path local legado)."""
    if not isinstance(val, str) or not val:
        return False
    return val.startswith("/uploads/anexos/") or ("supabase" in val and "/anexos/" in val)


# Janela de varredura do cleanup de anexos por job (dias). O ciclo roda a cada
# 10min: sem janela ele recarregava TODOS os jobs finalizados >24h (milhares,
# crescendo para sempre) e re-deletava os mesmos anexos por HTTP — ~80s por
# ciclo. Anexos mais antigos que a janela já são cobertos pelo sweep de orfãos
# (cleanup_orphan_anexos, 48h), então a janela só perde a garantia "24h" para
# jobs finalizados entre a janela e o sweep — inexistentes na prática.
ANEXOS_JOB_WINDOW_DAYS = int(os.environ.get("ANEXOS_JOB_WINDOW_DAYS", "3") or 3)

# Jobs cujos anexos já foram removidos por ESTE processo (id -> updated_at).
# Evita re-deletar (DELETE 404 no Storage) o mesmo job a cada ciclo enquanto
# ele permanece na janela. Memória limitada pela própria janela.
_ANEXOS_LIMPOS = {}


def cleanup_expired_attachments(db: Session):
    """
    Remove anexos de jobs concluidos ha mais de 24h (Supabase Storage bucket
    'anexos' ou filesystem local legado) + anexos orfaos com mais de 48h.
    """
    logger.info("Cleanup: Iniciando limpeza de anexos expirados...")

    # 1. Anexos associados a jobs finalizados ha mais de 24 horas
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        window_start = now - timedelta(days=ANEXOS_JOB_WINDOW_DAYS)
        # Poda o registro dos já limpos que saíram da janela
        for jid, upd in list(_ANEXOS_LIMPOS.items()):
            upd_aware = upd if upd.tzinfo else upd.replace(tzinfo=timezone.utc)
            if upd_aware < window_start:
                del _ANEXOS_LIMPOS[jid]
        completed_jobs = db.query(Job).filter(
            Job.status.in_(["success", "error"]),
            Job.updated_at < cutoff,
            Job.updated_at >= window_start,
            # Prefilter: só jobs cujo params menciona anexo (JSONB cast text)
            Job.params.isnot(None),
            cast(Job.params, Text).like('%"anexo%'),
        ).all()
        # Fecha a transação da leitura ANTES dos DELETEs HTTP no Storage —
        # manter a sessão 'idle in transaction' durante I/O lento faz o
        # Supavisor matar a conexão (incidente 2026-11-21).
        db.commit()

        removed_count = 0
        for job in completed_jobs:
            if not job.params or job.id in _ANEXOS_LIMPOS:
                continue

            # Marca antes de processar: DELETE é idempotente, então um crash
            # no meio só adia a remoção para o sweep de orfãos (48h).
            _ANEXOS_LIMPOS[job.id] = job.updated_at

            try:
                params = json.loads(job.params) if isinstance(job.params, str) else job.params
            except Exception:
                continue

            if not params or not isinstance(params, dict):
                continue

            urls_para_deletar = []
            for field in ("anexo_RM", "anexo_AI", "anexo_RC"):
                if _is_anexo_url(params.get(field)):
                    urls_para_deletar.append(params[field])

            anexos_list = params.get("anexos", [])
            if isinstance(anexos_list, list):
                for anx in anexos_list:
                    if isinstance(anx, dict) and _is_anexo_url(anx.get("caminho")):
                        urls_para_deletar.append(anx["caminho"])

            for url in urls_para_deletar:
                if storage_service.delete_anexo_by_url(url):
                    removed_count += 1
                    logger.info(f"Cleanup: anexo removido do job {job.id}: {url[:80]}")

        if removed_count > 0:
            logger.info(f"Cleanup: {removed_count} anexos de jobs finalizados removidos.")
    except Exception as e:
        logger.error(f"Cleanup: Erro ao limpar anexos de jobs finalizados: {e}")
        try: db.rollback()
        except Exception: pass

    # 2. Anexos orfaos (sem job) com mais de 48 horas
    try:
        orphan_count = storage_service.cleanup_orphan_anexos(max_age_seconds=48 * 3600)
        if orphan_count > 0:
            logger.info(f"Cleanup: {orphan_count} anexos orfaos removidos.")
    except Exception as e:
        logger.error(f"Cleanup: Erro ao limpar anexos orfaos: {e}")
