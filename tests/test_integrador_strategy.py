"""
QA-04/QA-05 — Testes do pacote de estratégia de integradores tipo agendamento
(DEV-03..DEV-05 do plano docs/plano_integradores_agendamento.md).

Cobrem: registry por espaço W, capabilities do catálogo, tradução canônica→rotina
(com 403 para OP não suportada — camada 1 do bloqueio) e montagem de jobs/params
da estratégia CLMF (paridade com as rotas antigas hardcoded 101).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from services.integrador_strategy import get_strategy, NullStrategy
from services.integrador_strategy.base import IntegradorAgendamentoStrategy
from services.integrador_strategy.clmf import AbaClmfStrategy
from services.integrador_strategy.evoluir import EvoluirStrategy
from services.integrador_strategy.canonico import (
    OP_REGISTRAR_FALTA,
    OP_SINCRONIZAR_AGENDAMENTOS,
    operacoes_canonicas_ativas,
    resolver_integrador_do_convenio,
    rotina_por_operacao_canonica,
)
from models import (
    Convenio,
    Integrador,
    UserConvenio,
    WorkerIntegrador,
    WorkerIntegradorOperacao,
)


def make_db(mapping=None):
    """db stub: db.query(Model|Model.coluna) devolve mock com all/first configurados."""
    mapping = mapping or {}

    def _key(q):
        if isinstance(q, type):
            return q
        return getattr(q, "class_", None) or getattr(getattr(q, "parent", None), "class_", None) or q

    def query(q):
        cfg = mapping.get(_key(q), {})
        m = MagicMock()
        m.all.return_value = cfg.get("all", [])
        m.filter.return_value.all.return_value = cfg.get("all", [])
        m.filter.return_value.first.return_value = cfg.get("first")
        return m

    db = MagicMock()
    db.query.side_effect = query
    return db


def _op(id_integrador, rotina, canonica, ativo=True):
    return SimpleNamespace(
        id_integrador=id_integrador, rotina=rotina,
        operacao_canonica=canonica, ativo=ativo
    )


CATALOGO_101 = [
    _op(101, "op1_importar_agendamentos", "sincronizar_agendamentos"),
    _op(101, "op2_consultar_carteirinha", "consultar_carteirinha"),
    _op(101, "op3_confirmar_agendamento", "confirmar_agendamento"),
    _op(101, "op4_registrar_falta", "registrar_falta"),
    _op(101, "op5_remover_falta", "remover_falta"),
    _op(101, "op6_atualizar_rc", "atualizar_rc"),
    _op(101, "op0_login", None),  # interna: sem canônica
]
CATALOGO_100 = [
    _op(100, "op1_importPacientes", "importar_pacientes"),
    _op(100, "op4_atualizarDataPTS", "atualizar_data_pts"),
    _op(100, "op5_ImportCorpoClinico", "importar_corpo_clinico"),
    _op(100, "op6_baixarFaturados", "baixar_faturados"),
    _op(100, "op7_consultaDocs", "consultar_documentos"),
]


class TestRegistry:

    def _db_com_integrador(self, tipo="agendamento", ativo=True):
        return make_db({
            WorkerIntegrador: {"first": SimpleNamespace(
                id_integrador=101, nome="ABA CLMF", sigla="ABA_CLMF",
                tipo_operacao=tipo, ativo=ativo
            )}
        })

    def test_101_resolve_clmf(self):
        strategy = get_strategy(self._db_com_integrador(), 101)
        assert isinstance(strategy, AbaClmfStrategy)
        assert strategy.id_integrador == 101

    def test_100_resolve_evoluir(self):
        db = make_db({WorkerIntegrador: {"first": SimpleNamespace(
            id_integrador=100, nome="Evoluir", sigla="EVOLUIR",
            tipo_operacao="agendamento", ativo=True
        )}})
        assert isinstance(get_strategy(db, 100), EvoluirStrategy)

    def test_desconhecido_retorna_null(self):
        db = make_db({WorkerIntegrador: {"first": None}})
        strategy = get_strategy(db, 999)
        assert isinstance(strategy, NullStrategy)

    def test_integrador_inativo_retorna_null(self):
        db = self._db_com_integrador(ativo=False)
        assert isinstance(get_strategy(db, 101), NullStrategy)

    def test_integrador_tipo_convenio_retorna_null(self):
        db = self._db_com_integrador(tipo="convenio")
        assert isinstance(get_strategy(db, 101), NullStrategy)

    def test_none_retorna_null(self):
        assert isinstance(get_strategy(make_db(), None), NullStrategy)


class TestCapabilitiesECatalogo:

    def test_capabilities_101_do_catalogo(self):
        db = make_db({WorkerIntegradorOperacao: {"all": CATALOGO_101}})
        strategy = AbaClmfStrategy(db)
        assert strategy.capabilities() == {
            "sincronizar_agendamentos", "consultar_carteirinha",
            "confirmar_agendamento", "registrar_falta", "remover_falta", "atualizar_rc",
        }

    def test_suporta_respeita_catalogo(self):
        db = make_db({WorkerIntegradorOperacao: {"all": CATALOGO_100}})
        strategy = EvoluirStrategy(db)
        assert strategy.suporta("importar_pacientes")
        assert not strategy.suporta(OP_REGISTRAR_FALTA)

    def test_operacoes_canonicas_ativas_ignora_canonica_nula(self):
        # O stub devolve o que o banco retornaria (condições ativo/IS NOT NULL são do
        # SQL); a lógica pura deve permanecer defensiva contra canônica nula (op0_login)
        # e devolver a lista ordenada.
        db = make_db({WorkerIntegradorOperacao: {"all": CATALOGO_101}})
        ops = operacoes_canonicas_ativas(db, 101)
        assert "op0_login" not in ops
        assert None not in ops
        assert ops == sorted(ops)
        assert "confirmar_agendamento" in ops

    def test_resolver_integrador_do_convenio_usa_mapeamento(self):
        # convenio 21 (Unimed Intercâmbio) → integrador 3 (Unimed Goiânia)
        db = make_db({
            Convenio: {"first": (3,)},
            WorkerIntegrador: {"first": SimpleNamespace(
                id_integrador=3, tipo_operacao="convenio", ativo=True
            )},
        })
        ing = resolver_integrador_do_convenio(db, 21)
        assert ing.id_integrador == 3

    def test_resolver_integrador_sem_mapeamento(self):
        db = make_db({Convenio: {"first": None}})
        assert resolver_integrador_do_convenio(db, 99) is None

    def test_rotina_por_operacao_canonica(self):
        db = make_db({WorkerIntegradorOperacao: {"first": _op(
            101, "op4_registrar_falta", "registrar_falta"
        )}})
        op = rotina_por_operacao_canonica(db, 101, OP_REGISTRAR_FALTA)
        assert op.rotina == "op4_registrar_falta"


class TestBloqueioCamada1:

    def test_op_nao_suportada_levanta_403(self):
        # Evoluir sem registrar_falta no catálogo
        db = make_db({WorkerIntegradorOperacao: {"all": CATALOGO_100, "first": None}})
        strategy = EvoluirStrategy(db)
        with pytest.raises(HTTPException) as exc:
            strategy.registrar_falta(1, [10, 11], 5, motivo_mapeado="1")
        assert exc.value.status_code == 403
        assert "não suportada pelo integrador" in exc.value.detail

    def test_null_strategy_bloqueia_tudo(self):
        strategy = NullStrategy(make_db(), 101)
        assert strategy.capabilities() == set()
        with pytest.raises(HTTPException) as exc:
            strategy._rotina_para(OP_REGISTRAR_FALTA)
        assert exc.value.status_code == 403


class TestAbaClmfCriacaoDeJobs:

    def _db(self, com_credencial=True, com_catalogo=True):
        uconv = SimpleNamespace(
            login="l", senha_criptografada="s", cod_prestador="p"
        ) if com_credencial else None
        return make_db({
            WorkerIntegrador: {"first": SimpleNamespace(
                id_integrador=101, nome="ABA CLMF", sigla="ABA_CLMF",
                tipo_operacao="agendamento", ativo=True
            )},
            WorkerIntegradorOperacao: {
                "all": CATALOGO_101 if com_catalogo else [],
                "first": _op(101, "op4_registrar_falta", "registrar_falta") if com_catalogo else None,
            },
            UserConvenio: {"first": uconv},
            Integrador: {"first": (101,)},
        })

    def test_registrar_falta_monta_job_com_paridade_101(self):
        db = self._db()
        job = AbaClmfStrategy(db).registrar_falta(
            user_id=7, portal_ids=[10, 11], id_paciente=5,
            motivo_mapeado="2", doc_justificativa=None
        )
        job_obj = db.add.call_args[0][0]
        assert job_obj is job
        assert job.id_convenio == 101
        assert job.rotina == "op4_registrar_falta"
        assert job.user_id == 7
        assert job.status == "pending"
        assert job.params["id_agendamento"] == [10, 11]
        assert job.params["id_paciente"] == 5
        assert job.params["tipo_desagendamento"] == "2"
        assert job.params["doc_justificativa"] == ""
        assert job.params["login"] == "l"
        assert job.params["senha_criptografada"] == "s"
        assert job.params["cod_prestador"] == "p"
        assert "webhook_url" in job.params

    def test_registrar_falta_sem_credencial_400(self):
        db = self._db(com_credencial=False)
        with pytest.raises(HTTPException) as exc:
            AbaClmfStrategy(db).registrar_falta(7, [10], 5, motivo_mapeado="2")
        assert exc.value.status_code == 400
        assert "Credenciais ABA CLMF não configuradas" in exc.value.detail

    def test_confirmar_agendamentos_num_situacao(self):
        db = make_db({
            WorkerIntegrador: {"first": SimpleNamespace(
                id_integrador=101, nome="ABA CLMF", sigla="ABA_CLMF",
                tipo_operacao="agendamento", ativo=True
            )},
            WorkerIntegradorOperacao: {
                "all": CATALOGO_101,
                "first": _op(101, "op3_confirmar_agendamento", "confirmar_agendamento"),
            },
            UserConvenio: {"first": SimpleNamespace(
                login="l", senha_criptografada="s", cod_prestador="p"
            )},
            Integrador: {"first": (101,)},
        })
        job = AbaClmfStrategy(db).confirmar_agendamentos(7, [33], remover=True)
        assert job.rotina == "op3_confirmar_agendamento"
        assert job.params["num_situacao"] == 0

    def test_sincronizar_exige_user_convenio(self):
        db = self._db(com_credencial=False)
        with pytest.raises(HTTPException) as exc:
            AbaClmfStrategy(db).sincronizar_agendamentos(7)
        assert exc.value.status_code == 400
        assert "não possui credenciais vinculadas" in exc.value.detail


class TestContratoBase:

    def test_base_e_abstrata(self):
        with pytest.raises(TypeError):
            IntegradorAgendamentoStrategy(make_db())

    def test_constantes_canonicas_estaveis(self):
        # Contrato cross-integrador: não renomear sem versionar o catálogo (0103)
        assert OP_REGISTRAR_FALTA == "registrar_falta"
        assert OP_SINCRONIZAR_AGENDAMENTOS == "sincronizar_agendamentos"
