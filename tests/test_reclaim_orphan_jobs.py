"""
QA reclaim de jobs órfãos (incidente 2026-10-23).

Frota do Local_worker morta deixava jobs 'processing' com lock sem dono — o
recover_stuck_jobs do dispatcher só roda com a frota viva (limiar 180min).
O hub (coordenador dos heartbeats) recupera: job stale E slot sem heartbeat.
Cobre services/cleanup_service.reclaim_orphaned_processing_jobs + helpers puros.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from services.cleanup_service import (
    reclaim_orphaned_processing_jobs,
    _porta_do_slot,
    _heartbeat_fresh,
)
from services import cleanup_service as cleanup_mod

AGORA = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)


class TestHelpersPuros:

    def test_porta_do_slot(self):
        assert _porta_do_slot("http://127.0.0.1:9000") == "9000"
        assert _porta_do_slot("http://10.0.0.5:9001/") == "9001"
        assert _porta_do_slot(None) is None
        assert _porta_do_slot("http://sem-porta") is None
        assert _porta_do_slot("http://host:abc") is None

    def test_heartbeat_fresh(self):
        assert _heartbeat_fresh(AGORA - timedelta(seconds=15), now=AGORA)
        assert _heartbeat_fresh(AGORA - timedelta(minutes=9), now=AGORA)
        assert not _heartbeat_fresh(AGORA - timedelta(minutes=11), now=AGORA)
        assert not _heartbeat_fresh(None, now=AGORA)
        # naive vindo do banco é tratado como UTC
        assert _heartbeat_fresh((AGORA - timedelta(seconds=15)).replace(tzinfo=None), now=AGORA)


def _job(job_id=1, updated_at=None, locked_by="http://127.0.0.1:9000",
         attempts=1, max_attempts=3):
    j = MagicMock()
    j.id = job_id
    j.user_id = 7
    j.status = "processing"
    j.locked_by = locked_by
    j.updated_at = updated_at or (AGORA - timedelta(hours=2))
    j.attempts = attempts
    j.max_attempts = max_attempts
    j.error_message = None
    return j


def _db(jobs, worker=None):
    """db.query(Job)...all() → jobs; db.query(Worker)...first() → worker."""
    db = MagicMock()
    q_job = MagicMock()
    q_job.filter.return_value.all.return_value = jobs
    q_worker = MagicMock()
    q_worker.filter.return_value.first.return_value = worker
    db.query.side_effect = [q_job, q_worker] if jobs else [q_job]
    return db


class TestReclaim:

    def test_slot_morto_recupera_para_pending(self):
        job = _job(attempts=1)
        worker = MagicMock(last_heartbeat=AGORA - timedelta(minutes=88))
        db = _db([job], worker)
        with _agora_fixa():
            resumo = reclaim_orphaned_processing_jobs(db)
        assert resumo["reclaimed_pending"] == 1
        assert job.status == "pending"
        assert job.locked_by is None
        assert db.commit.called

    def test_slot_vivo_nao_toca(self):
        job = _job()
        worker = MagicMock(last_heartbeat=AGORA - timedelta(seconds=12))
        db = _db([job], worker)
        with _agora_fixa():
            resumo = reclaim_orphaned_processing_jobs(db)
        assert resumo == {"reclaimed_pending": 0, "reclaimed_error": 0, "skipped_worker_vivo": 1}
        assert job.status == "processing"
        assert not db.commit.called

    def test_slot_desconhecido_recupera(self):
        # locked_by sem registro em worker.workers → ninguém heartbeata esse slot
        job = _job()
        db = _db([job], worker=None)
        with _agora_fixa():
            resumo = reclaim_orphaned_processing_jobs(db)
        assert resumo["reclaimed_pending"] == 1

    def test_tentativas_esgotadas_vao_para_error(self):
        job = _job(attempts=3, max_attempts=3)
        worker = MagicMock(last_heartbeat=AGORA - timedelta(hours=2))
        db = _db([job], worker)
        with _agora_fixa():
            resumo = reclaim_orphaned_processing_jobs(db)
        assert resumo["reclaimed_error"] == 1
        assert job.status == "error"
        assert "reclaim do hub" in job.error_message

    def test_query_vazia_nao_commita(self):
        db = _db([])
        with _agora_fixa():
            resumo = reclaim_orphaned_processing_jobs(db)
        assert resumo["reclaimed_pending"] == 0
        assert not db.commit.called

    def test_excecao_nao_propaga_e_da_rollback(self):
        db = MagicMock()
        db.query.side_effect = RuntimeError("db fora")
        with _agora_fixa():
            resumo = reclaim_orphaned_processing_jobs(db)
        assert resumo["reclaimed_pending"] == 0
        assert db.rollback.called


class _agora_fixa:
    """Congela datetime.now(utc) no módulo para asserts determinísticos."""

    def __enter__(self):
        self._orig = cleanup_mod.datetime
        class FakeDatetime:
            @staticmethod
            def now(tz=None):
                return AGORA
        cleanup_mod.datetime = FakeDatetime

    def __exit__(self, *a):
        cleanup_mod.datetime = self._orig
