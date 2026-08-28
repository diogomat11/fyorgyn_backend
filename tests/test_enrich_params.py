"""
QA-01 — Testes do enrich de params com config do convênio (BE-03).

Cobrem a injeção de prazo_pei_dias, guias_multiprestador e
procedimentos_habilitados (Unimed Goiânia, id_convenio=3) nos params do job,
incluindo idempotência (não sobrescrever payload) e cache por convenio.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.job_service import _enrich_params_with_convenio_config


def _fake_db(convenio):
    """Sessão fake: db.query(Convenio).filter(...).first() -> convenio."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = convenio
    return db


CONVENIO_GOIANIA = SimpleNamespace(
    id_convenio=3, prazo_pei_dias=90, guias_multiprestador=True
)
CONVENIO_IPASGO = SimpleNamespace(
    id_convenio=6, prazo_pei_dias=180, guias_multiprestador=False
)


class TestEnrichParamsConvenioConfig:

    def test_injeta_prazo_e_multiprestador(self):
        db = _fake_db(CONVENIO_GOIANIA)
        p = _enrich_params_with_convenio_config(db, 3, {})
        assert p["prazo_pei_dias"] == 90
        assert p["guias_multiprestador"] is True

    def test_multiprestador_false_para_convenios_restritos(self):
        db = _fake_db(CONVENIO_IPASGO)
        p = _enrich_params_with_convenio_config(db, 6, {})
        assert p["guias_multiprestador"] is False
        assert p["prazo_pei_dias"] == 180

    def test_idempotente_nao_sobrescreve_payload(self):
        db = _fake_db(CONVENIO_GOIANIA)
        p = _enrich_params_with_convenio_config(db, 3, {"prazo_pei_dias": 365, "guias_multiprestador": False})
        assert p["prazo_pei_dias"] == 365
        assert p["guias_multiprestador"] is False

    def test_convenio_inexistente_nao_altera_params(self):
        db = _fake_db(None)
        p = _enrich_params_with_convenio_config(db, 999, {"x": 1})
        assert p == {"x": 1}

    def test_id_convenio_nulo_retorna_intacto(self):
        db = _fake_db(CONVENIO_GOIANIA)
        p = _enrich_params_with_convenio_config(db, None, {"x": 1})
        assert p == {"x": 1}

    def test_cache_evita_queries_repetidas(self):
        db = _fake_db(CONVENIO_GOIANIA)
        cache = {}
        _enrich_params_with_convenio_config(db, 3, {}, cache)
        queries_apos_primeira = db.query.call_count
        _enrich_params_with_convenio_config(db, 3, {}, cache)
        # 2a chamada usa o cache: nenhuma query nova
        assert db.query.call_count == queries_apos_primeira

    def test_sem_cache_tambem_funciona(self):
        db = _fake_db(CONVENIO_GOIANIA)
        p = _enrich_params_with_convenio_config(db, 3, {})
        assert p["prazo_pei_dias"] == 90

    def test_procedimentos_habilitados_apenas_conv3(self):
        db = _fake_db(CONVENIO_GOIANIA)
        # db.query(...) encadeado: 1o p/ convenio, 2o p/ procedimentos
        db.query.return_value.filter.return_value.all.return_value = [("10101012",), ("10101019",)]
        p = _enrich_params_with_convenio_config(db, 3, {})
        assert set(p["procedimentos_habilitados"]) == {"10101012", "10101019"}

        db_ipasgo = _fake_db(CONVENIO_IPASGO)
        p2 = _enrich_params_with_convenio_config(db_ipasgo, 6, {})
        assert "procedimentos_habilitados" not in p2
