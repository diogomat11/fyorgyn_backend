"""
Retenção de worker.jobs e worker.logs (2026-08).

As tabelas só cresciam desde a criação do sistema (jobs finalizados e logs
acumulavam indefinidamente). Regras (env, defaults conservadores):

- pending  > PENDING_MAX_AGE_HOURS (default 24h): job do lote diário do cron que
  a frota não processou é stale — apagar (regra do PO: após ~1 dia sem
  processar, apagar; evita executar consulta de ontem amanhã);
- success+consumido e error > JOBS_RETENTION_DAYS (default 30d);
- logs > LOGS_RETENTION_DAYS (default 30d).

Deletes em LOTES (LIMIT) para não travar as tabelas; registra Log INFO de
resumo somente quando algo foi expurgado. Executado pelo run_cleanup_loop
(a cada 10 min) do main.py. FKs dependentes de worker.jobs são SET NULL/CASCADE
(verificado em 2026-08-27) — o delete propaga com segurança.
"""
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

PENDING_MAX_AGE_HOURS = int(os.getenv("PENDING_MAX_AGE_HOURS", "24"))
JOBS_RETENTION_DAYS = int(os.getenv("JOBS_RETENTION_DAYS", "30"))
LOGS_RETENTION_DAYS = int(os.getenv("LOGS_RETENTION_DAYS", "30"))
BATCH = int(os.getenv("RETENTION_BATCH", "2000"))
MAX_LOOPS = 50  # teto por ciclo de 10 min: até 100k linhas por categoria

# Critérios fixos (sem input externo) — interpolados apenas os nomes de tabela
_WHERE_PENDING_STALE = (
    "status = 'pending' AND created_at < now() - make_interval(hours => :idade)"
)
_WHERE_JOBS_FINALIZADOS = (
    "((status = 'success' AND result_consumed = true) OR status = 'error') "
    "AND updated_at < now() - make_interval(days => :idade)"
)
_WHERE_LOGS = "created_at < now() - make_interval(days => :idade)"


def _purge_in_lotes(db: Session, tabela: str, where: str, params: dict) -> int:
    """DELETE em lotes de BATCH até esvaziar o critério (ou atingir MAX_LOOPS)."""
    total = 0
    for _ in range(MAX_LOOPS):
        result = db.execute(text(
            f"DELETE FROM {tabela} WHERE id IN ("
            f"SELECT id FROM {tabela} WHERE {where} LIMIT :lote)"
        ), {**params, "lote": BATCH})
        apagados = result.rowcount or 0
        total += apagados
        if apagados < BATCH:
            break
    return total


def run_retention(db: Session) -> dict:
    """Executa todas as regras de retenção e devolve o resumo de expurgos."""
    resumo = {
        "pending_stale": _purge_in_lotes(db, "worker.jobs", _WHERE_PENDING_STALE,
                                         {"idade": PENDING_MAX_AGE_HOURS}),
        "jobs_finalizados": _purge_in_lotes(db, "worker.jobs", _WHERE_JOBS_FINALIZADOS,
                                            {"idade": JOBS_RETENTION_DAYS}),
        "logs": _purge_in_lotes(db, "worker.logs", _WHERE_LOGS,
                                {"idade": LOGS_RETENTION_DAYS}),
    }
    db.commit()
    if any(resumo.values()):
        db.execute(text(
            "INSERT INTO worker.logs (job_id, user_id, level, message) "
            "VALUES (NULL, NULL, 'INFO', :m)"
        ), {"m": f"[RETENTION] pending_stale={resumo['pending_stale']} (> {PENDING_MAX_AGE_HOURS}h); "
                 f"jobs_finalizados={resumo['jobs_finalizados']} (> {JOBS_RETENTION_DAYS}d); "
                 f"logs={resumo['logs']} (> {LOGS_RETENTION_DAYS}d)."})
        db.commit()
    return resumo
