"""
QA — Dedup de base_guias no sync (incidente 2026-09-02, job #10791) e guards do webhook.

Cobrem:
- pré-check de duplicidade SEM filtro de user_id: linha homônima de outro tenant
  é encontrada e a guia é pulada (antes: insert -> UniqueViolation -> quarentena);
- fallback de IntegrityError com savepoint: corrida com outro runner converte o
  insert em update da linha existente em vez de derrubar o job;
- claim atômico (_claim_job_for_sync): só um runner conquista cada job;
- webhook (submit_job_result): job success+já consumido com mesmo payload não é
  reaberto; payload diferente reabre e agenda sync; job inexistente resolve
  user_id em vez de usar o hardcoded 1.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

import models as m
from services.guias_sync_service import (
    _apply_guia_update,
    _claim_job_for_sync,
    bulk_upsert_guias_from_json,
)


GUIA_OK = {
    "numero_guia": "25387101",
    "status": "Autorizado",
    "codigo_terapia": "00011193",
    "senha": "S1",
    "qtde_solicitada": 10,
    "qtde_autorizada": 8,
}


def _linha_base(**kw):
    base = dict(
        id=9001, user_id=1, carteirinha_id=5611, id_convenio=6,
        guia="25387101", guia_prestador=None, codigo_terapia="00011193",
        nome_terapia=None, senha=None, status_guia="Antigo",
        data_solicitacao=None, data_autorizacao=None, validade=None,
        qtde_solicitada=None, sessoes_autorizadas=None, sessoes_realizadas=None,
        saldo=None, timestamp_captura=None, codigo_beneficiario=None,
        cod_prestador=None, valida_prestador=None, updated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _db_com_filas(base_guias_pre_check=(), raced=None, solicitacao=None):
    """Sessão mock com filas configuradas por model (padrão da suíte)."""
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is m.Carteirinha:
            q.filter.return_value.all.return_value = []
        elif model is m.BaseGuia:
            q.filter.return_value.all.return_value = list(base_guias_pre_check)
            q.filter.return_value.first.return_value = raced
        elif model is m.Solicitacao:
            q.filter.return_value.first.return_value = solicitacao
        return q

    db.query.side_effect = _query
    return db


class TestPreCheckSemFiltroDeUser:

    def test_linha_de_outro_tenant_e_pulada_em_vez_de_duplicar(self):
        # Linha existe no banco com user_id=99; job roda para user_id=1.
        # Antes: o pré-check filtrava por user_id, não via a linha, inseria e
        # violava uq_guia_conv_ter_cart (quarentena). Agora: encontra e pula.
        alheia = _linha_base(id=9001, user_id=99)
        db = _db_com_filas(base_guias_pre_check=[alheia])

        res = bulk_upsert_guias_from_json(db, [GUIA_OK], id_convenio=6, user_id=1)

        assert res["inserted"] == 0
        assert res["updated"] == 0
        assert res["skipped"] == 1
        db.add.assert_not_called()

    def test_linha_do_proprio_user_e_atualizada(self):
        propria = _linha_base(id=9002, user_id=1)
        db = _db_com_filas(base_guias_pre_check=[propria])

        res = bulk_upsert_guias_from_json(db, [GUIA_OK], id_convenio=6, user_id=1)

        assert res["inserted"] == 0
        assert res["updated"] == 1
        assert propria.status_guia == "Autorizado"
        assert propria.senha == "S1"


class TestFallbackIntegrityError:

    def test_corrida_converte_insert_em_update(self):
        # Pré-check vazio; outro runner grava a chave entre o SELECT e o flush.
        raced = _linha_base(id=9003, user_id=None)  # órfã: adotável
        db = _db_com_filas(base_guias_pre_check=[], raced=raced)

        nested = MagicMock()
        db.begin_nested.return_value = nested
        db.flush.side_effect = IntegrityError("INSERT ...", {}, Exception("dup"))

        res = bulk_upsert_guias_from_json(db, [GUIA_OK], id_convenio=6, user_id=1)

        assert res["inserted"] == 0
        assert res["updated"] == 1
        assert res["skipped"] == 0
        nested.rollback.assert_called_once()
        assert raced.status_guia == "Autorizado"
        # job não pode ter sido derrubado: sem rollback da transação inteira
        db.rollback.assert_not_called()

    def test_corrida_com_linha_de_outro_tenant_e_pulada(self):
        raced = _linha_base(id=9004, user_id=99)
        db = _db_com_filas(base_guias_pre_check=[], raced=raced)

        nested = MagicMock()
        db.begin_nested.return_value = nested
        db.flush.side_effect = IntegrityError("INSERT ...", {}, Exception("dup"))

        res = bulk_upsert_guias_from_json(db, [GUIA_OK], id_convenio=6, user_id=1)

        assert res["inserted"] == 0
        assert res["updated"] == 0
        assert res["skipped"] == 1
        db.rollback.assert_not_called()


class TestApplyGuiaUpdate:

    def test_user_id_none_nao_limpa_tenant_da_linha(self):
        linha = _linha_base(user_id=7)
        _apply_guia_update(linha, {"senha": "X"}, user_id=None)
        assert linha.user_id == 7
        assert linha.senha == "X"

    def test_user_id_real_adopta_linha_orfa(self):
        linha = _linha_base(user_id=None)
        _apply_guia_update(linha, {"senha": "X"}, user_id=5)
        assert linha.user_id == 5


class TestClaimAtomico:

    def _db(self, rowcount):
        db = MagicMock()
        db.execute.return_value = MagicMock(rowcount=rowcount)
        return db

    def test_claim_bem_sucedido(self):
        db = self._db(rowcount=1)
        assert _claim_job_for_sync(db, 10791) is True
        db.commit.assert_called_once()

    def test_job_ja_conquistado_por_outro_runner(self):
        db = self._db(rowcount=0)
        assert _claim_job_for_sync(db, 10791) is False


class TestWebhookSubmitJobResult:
    """Guards de reabertura de consumo e resolução de user_id (fim do user_id=1)."""

    def _call(self, job, payload):
        from routes.jobs import submit_job_result
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = job
        bt = MagicMock()
        resp = submit_job_result(job_id=10791, background_tasks=bt, payload=payload, db=db)
        return job, bt, resp

    def test_job_consumido_com_mesmo_payload_nao_e_reaberto(self):
        payload = {"job_id": 10791, "status": "success", "data": [1, 2, 3]}
        job = SimpleNamespace(status="success", result_consumed=True, result_data=payload,
                              updated_at=None)
        _, bt, resp = self._call(job, payload)

        assert job.result_consumed is True  # não resetado
        bt.add_task.assert_not_called()
        assert resp["status"] == "success"

    def test_payload_diferente_reabre_e_agenda_sync(self):
        job = SimpleNamespace(status="success", result_consumed=True,
                              result_data={"data": [1]}, updated_at=None)
        _, bt, _ = self._call(job, {"data": [1, 2]})

        assert job.result_consumed is False
        bt.add_task.assert_called_once()

    def test_job_novo_resolve_user_id_via_carteirinha(self):
        from routes.jobs import submit_job_result
        db = MagicMock()
        # primeira query (Job) não acha; segunda (Carteirinha) acha user 14
        job_q = MagicMock()
        job_q.filter.return_value.first.return_value = None
        cart_q = MagicMock()
        cart_q.filter.return_value.first.return_value = SimpleNamespace(id=5611, user_id=14)
        db.query.side_effect = lambda model: job_q if model is m.Job else cart_q

        submit_job_result(job_id=999, background_tasks=MagicMock(),
                          payload={"carteirinha_id": 5611, "data": []}, db=db)

        criado = db.add.call_args[0][0]
        assert criado.user_id == 14  # antes: hardcoded 1
