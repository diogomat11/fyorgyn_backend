"""
QA — MFA Bradesco/Faturi (plano_mfa_bradesco.md §7, QA-01..QA-07).

Cobre a lógica pura do hub (sem banco real — padrão da casa):
- estados MFA derivados das colunas (mfa_estado);
- validações do POST /jobs/{id}/mfa em ordem (posse multi-tenant, expiração,
  já-consumido, job fora de processing, código inválido);
- cancelamento e idempotência de reenvio pré-consumo.
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.jobs import MFA_MAX_CYCLES, mfa_estado, mfa_submit, mfa_cancel  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)


def job_mfa(**kw):
    base = dict(
        id=1, user_id=14, status="processing",
        mfa_required_at=None, mfa_expires_at=None, mfa_code=None,
        mfa_submitted_at=None, mfa_consumed_at=None,
        mfa_cancelled=False, mfa_attempts=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── mfa_estado (função pura) ────────────────────────────────────────────────

def test_estado_none_sem_mfa():
    assert mfa_estado(job_mfa(), NOW)["estado"] == "none"


def test_estado_required_com_prazo_e_ciclo():
    job = job_mfa(mfa_required_at=NOW - timedelta(seconds=10),
                  mfa_expires_at=NOW + timedelta(seconds=290))
    est = mfa_estado(job, NOW)
    assert est["estado"] == "required"
    assert est["cycle"] == 1  # attempts=0 → ciclo 1
    assert est["max_cycles"] == MFA_MAX_CYCLES


def test_estado_required_ciclo2_apos_codigo_invalido():
    job = job_mfa(mfa_required_at=NOW - timedelta(seconds=5),
                  mfa_expires_at=NOW + timedelta(seconds=295),
                  mfa_attempts=1)
    assert mfa_estado(job, NOW)["cycle"] == 2


def test_estado_provided_aguardando_worker():
    job = job_mfa(mfa_required_at=NOW, mfa_expires_at=NOW + timedelta(seconds=300),
                  mfa_code="123456")
    assert mfa_estado(job, NOW)["estado"] == "provided"


def test_estado_done_tem_precedencia_sobre_provided():
    """Semântica do consumo (incidentes 20646/20649): o worker marca
    mfa_consumed_at SEM apagar o código (RETURNING devolve valor pós-update) —
    estado deve ser done mesmo com mfa_code ainda presente."""
    job = job_mfa(mfa_required_at=NOW, mfa_code="123456", mfa_consumed_at=NOW)
    assert mfa_estado(job, NOW)["estado"] == "done"


def test_estado_expired_apos_prazo():
    job = job_mfa(mfa_required_at=NOW - timedelta(seconds=301),
                  mfa_expires_at=NOW - timedelta(seconds=1))
    assert mfa_estado(job, NOW)["estado"] == "expired"


def test_estado_cancelled_e_done():
    assert mfa_estado(job_mfa(mfa_required_at=NOW, mfa_cancelled=True), NOW)["estado"] == "cancelled"
    assert mfa_estado(job_mfa(mfa_required_at=NOW, mfa_consumed_at=NOW), NOW)["estado"] == "done"


# ── mfa_submit (rota chamada como função; db/user mockados) ────────────────

def _db_com_job(job):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = job
    return db


USER_DONO = SimpleNamespace(id=14, is_admin=False, parent_user_id=None)
USER_OUTRO = SimpleNamespace(id=99, is_admin=False, parent_user_id=None)


def _futuro(seg=300):
    """Prazo relativo ao relógio REAL (mfa_submit valida com datetime.now)."""
    return datetime.now(timezone.utc) + timedelta(seconds=seg)


def _passado(seg=301):
    return datetime.now(timezone.utc) - timedelta(seconds=seg)


def _submit(job, user=USER_DONO, codigo="123456"):
    from routes.jobs import MfaSubmitRequest
    return mfa_submit(1, MfaSubmitRequest(codigo=codigo), current_user=user, db=_db_com_job(job))


def test_submit_outro_tenant_recebe_404():
    """QA-06 — posse multi-tenant: outro usuário não vê existência do job."""
    job = job_mfa(mfa_required_at=_passado(10), mfa_expires_at=_futuro())
    with pytest.raises(HTTPException) as e:
        _submit(job, user=USER_OUTRO)
    assert e.value.status_code == 404
    assert job.mfa_code is None  # nada gravado


def test_submit_codigo_invalido_400():
    for codigo in ("12a4", "123", "12345678901", ""):
        with pytest.raises(HTTPException) as e:
            _submit(job_mfa(), codigo=codigo)
        assert e.value.status_code == 400


def test_submit_job_fora_de_processing_400():
    job = job_mfa(status="success", mfa_required_at=_passado(10),
                  mfa_expires_at=_futuro())
    with pytest.raises(HTTPException) as e:
        _submit(job)
    assert "não está em execução" in e.value.detail


def test_submit_expirado_400():
    """QA-07 — submit após o prazo do ciclo."""
    job = job_mfa(mfa_required_at=_passado(), mfa_expires_at=_passado(1))
    with pytest.raises(HTTPException) as e:
        _submit(job)
    assert "Tempo esgotado" in e.value.detail


def test_submit_codigo_ja_consumido_400():
    """QA-05 — duplo submit após consumo não repete o código."""
    job = job_mfa(mfa_required_at=_passado(10), mfa_expires_at=_futuro(),
                  mfa_consumed_at=_passado(5), mfa_code=None)
    with pytest.raises(HTTPException) as e:
        _submit(job)
    assert "já em processamento" in e.value.detail


def test_submit_happy_path_grava_codigo_e_marca_submitted():
    job = job_mfa(mfa_required_at=_passado(10), mfa_expires_at=_futuro())
    _submit(job)
    assert job.mfa_code == "123456"
    assert job.mfa_submitted_at is not None


def test_submit_reenvio_pre_consumo_sobrescreve_idempotente():
    """QA-05 — duplo clique antes do consumo: sobrescreve, sem erro."""
    job = job_mfa(mfa_required_at=_passado(10), mfa_expires_at=_futuro(),
                  mfa_code="111111", mfa_submitted_at=_passado(2))
    _submit(job, codigo="222222")
    assert job.mfa_code == "222222"


# ── mfa_cancel ──────────────────────────────────────────────────────────────

def test_cancel_happy_path():
    job = job_mfa(mfa_required_at=_passado(10), mfa_expires_at=_futuro())
    mfa_cancel(1, current_user=USER_DONO, db=_db_com_job(job))
    assert job.mfa_cancelled is True


def test_cancel_sem_solicitacao_400():
    with pytest.raises(HTTPException) as e:
        mfa_cancel(1, current_user=USER_DONO, db=_db_com_job(job_mfa()))
    assert e.value.status_code == 400
