"""
Cron diário de carteirinhas por integrador.

Adaptado do clmf_hub_basic (backend/main.py run_unimed_cron_loop), com melhorias:
- Configuração em banco (public.cron_configs) com painel admin (enable/disable/horário).
- Guarda de execução (ultimo_run) NO BANCO -> idempotente após restart do backend
  (o clmf usava globals em memória e duplicava em restart no mesmo dia).
- Catch-up: se o backend estava fora do ar no horário agendado, executa ao subir
  (agora >= horário E ultimo_run < hoje).
- Timezone explícita por config (servidor Render roda em UTC).
- NÃO importa a etapa de limpeza de guias do clmf (base_guias aqui é multi-tenant).

Fluxo: para cada integrador habilitado, cria jobs `rotina` (default op1_consulta)
para TODAS as carteirinhas ativas dos convênios do integrador, agrupadas por
(user_id, id_convenio) para enriquecimento de credenciais (user_convenios).
"""
from datetime import date, datetime, time as dt_time
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from models import Carteirinha, Convenio, CronConfig, UserConvenio


def _cron_logger(msg: str):
    # Mesmo padrão dos loops de background do main.py (print -> stdout/Render log)
    print(f"[CRON] {msg}")


def list_crons(db: Session) -> List[dict]:
    """Lista configs de cron com nome do integrador (para o painel admin)."""
    from models import WorkerIntegrador
    integradores = {i.id_integrador: i.nome for i in db.query(WorkerIntegrador).all()}
    crons = db.query(CronConfig).order_by(CronConfig.id_integrador).all()
    return [
        {
            "id": c.id,
            "id_integrador": c.id_integrador,
            "nome_integrador": integradores.get(c.id_integrador, f"Integrador {c.id_integrador}"),
            "enabled": bool(c.enabled),
            "horario": c.horario.strftime("%H:%M") if c.horario else "23:01",
            "fuso": c.fuso or "America/Sao_Paulo",
            "rotina": c.rotina or "op1_consulta",
            "ultimo_run": c.ultimo_run.isoformat() if c.ultimo_run else None,
        }
        for c in crons
    ]


def update_cron_config(
    db: Session,
    cron_id: int,
    enabled: Optional[bool] = None,
    horario: Optional[str] = None,
    rotina: Optional[str] = None,
) -> CronConfig:
    """Atualiza habilitação/horário/rotina de um cron (painel admin)."""
    cron = db.query(CronConfig).filter(CronConfig.id == cron_id).first()
    if not cron:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Config de cron não encontrada.")
    if enabled is not None:
        cron.enabled = enabled
    if horario is not None:
        try:
            h, m = horario.strip().split(":")
            cron.horario = dt_time(int(h), int(m))
        except Exception:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Horário inválido. Use formato HH:MM.")
    if rotina is not None and rotina.strip():
        cron.rotina = rotina.strip()
    db.commit()
    db.refresh(cron)
    return cron


def _ativos_by_grupo(db: Session, id_integrador: int) -> dict:
    """
    Carteirinhas ativas (não temporárias) dos convênios do integrador,
    agrupadas por (user_id, id_convenio) -> lista de ids de carteirinha.
    """
    convenio_ids = [
        c[0] for c in db.query(Convenio.id_convenio).filter(Convenio.id_integrador == id_integrador).all()
    ]
    if not convenio_ids:
        return {}

    rows = db.query(Carteirinha.id, Carteirinha.user_id, Carteirinha.id_convenio).filter(
        Carteirinha.id_convenio.in_(convenio_ids),
        Carteirinha.status == "ativo",
        Carteirinha.is_temporary.is_(False),
    ).all()

    grupos: dict = {}
    for cart_id, user_id, conv_id in rows:
        grupos.setdefault((user_id, conv_id), []).append(cart_id)
    return grupos


def run_cron_jobs(db: Session, cron: CronConfig) -> dict:
    """
    Cria jobs (rotina do cron) para todas as carteirinhas ativas dos convênios
    do integrador. Grupos sem credenciais em user_convenios são pulados (log).
    """
    from services.job_service import create_jobs_bulk

    resumo = {"jobs_criados": 0, "carteirinhas": 0, "grupos_pulados_sem_credencial": 0}
    grupos = _ativos_by_grupo(db, cron.id_integrador)
    if not grupos:
        _cron_logger(f"Integrador {cron.id_integrador}: nenhuma carteirinha ativa encontrada.")
        return resumo

    for (user_id, conv_id), cart_ids in sorted(grupos.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
        if user_id is None:
            resumo["grupos_pulados_sem_credencial"] += 1
            _cron_logger(f"Integrador {cron.id_integrador}: grupo convenio={conv_id} sem user_id (carteirinha global) - pulado.")
            continue
        tem_credencial = db.query(UserConvenio.id).filter(
            UserConvenio.user_id == user_id,
            UserConvenio.id_convenio == conv_id,
        ).first()
        if not tem_credencial:
            resumo["grupos_pulados_sem_credencial"] += 1
            _cron_logger(f"Integrador {cron.id_integrador}: user={user_id} convenio={conv_id} sem credenciais - pulado.")
            continue
        try:
            criados = create_jobs_bulk(db, cart_ids, id_convenio=conv_id, rotina=cron.rotina, user_id=user_id)
            resumo["jobs_criados"] += criados or 0
            resumo["carteirinhas"] += len(cart_ids)
        except Exception as e:
            _cron_logger(f"Integrador {cron.id_integrador}: erro ao criar jobs user={user_id} convenio={conv_id}: {e}")

    _cron_logger(
        f"Integrador {cron.id_integrador} ({cron.rotina}): {resumo['jobs_criados']} jobs / "
        f"{resumo['carteirinhas']} carteirinhas ({resumo['grupos_pulados_sem_credencial']} grupos pulados)."
    )
    return resumo


def run_due_crons(db: Session, now: Optional[datetime] = None) -> List[dict]:
    """
    Executa os crons habilitados cujo horário chegou (com catch-up) e que ainda
    não rodaram hoje. Marca ultimo_run no banco ANTES de criar os jobs para
    garantir idempotência mesmo se a criação falhar no meio.

    `now` (opcional, tz-aware) permite injetar o relógio em testes; por padrão
    usa o horário corrente no fuso de cada cron.
    """
    resultados = []
    crons = db.query(CronConfig).filter(CronConfig.enabled.is_(True)).all()

    for cron in crons:
        try:
            tz = ZoneInfo(cron.fuso or "America/Sao_Paulo")
        except Exception:
            tz = ZoneInfo("America/Sao_Paulo")

        now_local = now.astimezone(tz) if now is not None else datetime.now(tz)
        hoje = now_local.date()
        horario = cron.horario or dt_time(23, 1)

        ja_rodou_hoje = cron.ultimo_run is not None and cron.ultimo_run >= hoje
        if ja_rodou_hoje or now_local.time() < horario:
            continue

        _cron_logger(f"Disparando cron integrador={cron.id_integrador} rotina={cron.rotina} "
                     f"horario={horario.strftime('%H:%M')} tz={tz.key}.")
        # Marca antes de executar (idempotência pós-crash)
        cron.ultimo_run = hoje
        db.commit()

        resumo = run_cron_jobs(db, cron)
        resultados.append({"id_integrador": cron.id_integrador, **resumo})

    return resultados


def run_cron_by_id(db: Session, cron_id: int, force: bool = True) -> dict:
    """Executa um cron específico imediatamente (botão 'Executar agora' do painel)."""
    cron = db.query(CronConfig).filter(CronConfig.id == cron_id).first()
    if not cron:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Config de cron não encontrada.")

    resumo = run_cron_jobs(db, cron)
    if force:
        cron.ultimo_run = date.today()
        db.commit()
    return {"id_integrador": cron.id_integrador, **resumo}
