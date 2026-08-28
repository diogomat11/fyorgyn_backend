"""
QA retenção + cron (2026-08-27).

Cobrem services/retention_service (expurgo em lotes, resumo com Log apenas
quando algo foi apagado) e o marcador de origem dos jobs criados pelo lote
diário do cron (cron_service.run_cron_jobs).
"""
import json
from unittest.mock import MagicMock, patch, call

from services.retention_service import run_retention, _purge_in_lotes
from services import cron_service
from models import CronConfig


def _db_com_rowcounts(rowcounts):
    """db.execute devolve mocks com rowcount em sequência por chamada."""
    db = MagicMock()
    results = [MagicMock(rowcount=rc) for rc in rowcounts]
    db.execute.side_effect = results
    return db


class TestPurgeInLotes:

    def test_purga_em_lotes_ate_esvaziar(self):
        # 1º lote cheio (2000), 2º parcial (500) -> para
        db = _db_com_rowcounts([2000, 500])
        total = _purge_in_lotes(db, "worker.jobs", "status='pending'", {"idade": 24})
        assert total == 2500
        assert db.execute.call_count == 2

    def test_lote_unico_menor_que_batch(self):
        db = _db_com_rowcounts([7])
        assert _purge_in_lotes(db, "worker.logs", "created_at < now()", {"idade": 30}) == 7
        assert db.execute.call_count == 1


class TestRunRetention:

    def test_resumo_sem_log_quando_nada_apagado(self):
        db = _db_com_rowcounts([0, 0, 0])
        resumo = run_retention(db)
        assert resumo == {"pending_stale": 0, "jobs_finalizados": 0, "logs": 0}
        # 3 deletes + commit; sem INSERT de resumo
        inserts = [c for c in db.execute.call_args_list
                   if "INSERT INTO worker.logs" in str(getattr(c.args[0], "text", ""))]
        assert not inserts

    def test_resumo_com_log_quando_apagou(self):
        # logs=3000 → dois lotes (2000 cheio + 1000 final)
        db = _db_com_rowcounts([583, 120, 2000, 1000, 1])  # 5º = INSERT do Log de resumo
        resumo = run_retention(db)
        assert resumo == {"pending_stale": 583, "jobs_finalizados": 120, "logs": 3000}
        inserts = [c for c in db.execute.call_args_list
                   if "INSERT INTO worker.logs" in str(getattr(c.args[0], "text", ""))]
        assert inserts, "deve registrar Log INFO de resumo quando expurga"


class TestCronMarcadorOrigem:

    def test_run_cron_jobs_marca_origem_cron(self):
        cron = CronConfig(id=1, id_integrador=3, rotina="op1_consulta",
                          enabled=True, horario=None)
        db = MagicMock()
        # _ativos_by_grupo -> 1 grupo com 2 carteirinhas; credencial presente
        db.query.return_value.filter.return_value.all.return_value = [
            (10, 7, 3), (11, 7, 3)   # (cart_id, user_id, conv_id)
        ]
        db.query.return_value.filter.return_value.first.return_value = True

        with patch("services.job_service.create_jobs_bulk", return_value=2) as bulk:
            resumo = cron_service.run_cron_jobs(db, cron)

        assert resumo["jobs_criados"] == 2
        kwargs = bulk.call_args.kwargs
        params = json.loads(kwargs["params"])
        assert params["origem_cron"] is True
        assert params["cron_id"] == 1
        assert kwargs["rotina"] == "op1_consulta"
        assert kwargs["user_id"] == 7
