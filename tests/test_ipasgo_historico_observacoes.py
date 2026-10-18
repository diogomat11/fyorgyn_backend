"""
QA — Histórico de Observações do regulador IPASGO (OP11/OP15) e ajustes da
aba Solicitações.

Cobrem:
- build_historico_param: o Base64 do query param `param` bate byte-a-byte com a
  requisição real capturada do portal (guia 26106190 / prestador 00631201102692873601);
- parse_aspnet_datetime: '/Date(1791314605000)/' → 2026-10-06T16:23:25-03:00
  (fuso exibido pelo portal);
- normalizar_historico: payload real do GetHistoricoObservacao (3 itens) vira a
  lista interna ordenada por DataHora DESC com os campos Usuario/DataHora/Login/
  Observacao mapeados;
- bulk_upsert_guias_from_json: item não-autorizado com historico_observacoes e
  guia_prestador persiste as duas colunas na solicitacao;
- _serialize_solicitacao_row: a Data Solicit. exibida vem da coluna
  data_solicitacao (OP11), não da data de importação (created_at).
"""
import sys
import os
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Módulos puros do executor IPASGO (Local_worker) sem dependência de Selenium
_CORE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..",
                 "Local_worker", "Worker", "6-ipasgo", "core")
)
sys.path.insert(0, _CORE_DIR)

from historico_observacoes import (  # noqa: E402
    build_historico_param,
    normalizar_historico,
    parse_aspnet_datetime,
)

# ── Dados reais capturados do portal (requisição do usuário) ──
GUIA_OPERADORA = "26106190"
GUIA_PRESTADOR = "00631201102692873601"
PARAM_BASE64 = (
    "eyJOdW1lcm9HdWlhT3BlcmFkb3JhIjoiMjYxMDYxOTAiLCJOdW1lcm9HdWlhUHJlc3RhZG9yIjoi"
    "MDA2MzEyMDExMDI2OTI4NzM2MDEifQ=="
)

HISTORICO_CRU = [
    {
        "NumeroGuiaOperadora": 26106190,
        "NumeroGuiaPrestador": GUIA_PRESTADOR,
        "Usuario": None,
        "DataHora": "/Date(1791314605000)/",
        "Observacao": "Encaminhado pendência através do E-mail:  EVOLUIRCDI@OUTLOOK.COM<br><br>Prezados, Conforme ORIENTAÇÃO DA PORTARIA NORMATIVA nº 4/2023 - IPASGO/DAS-06148 e COMUNICADO enviado pelo IPASGO as documentações necessárias para análise são:<br>- Além da Solicitação Médica é necessário Relatório Médico relacionados ao acompanhamento do TEA emitidos por profissional médico regularmente habilitado, om validade de até 12 meses<br><br><br>",
        "Login": "Auditoria IPASGO",
        "LiberaPendenteDoc": False,
        "LiberaAuditoriaOperadora": False,
        "LiberaAguardandoBeneficiario": False,
        "Id": 0,
    },
    {
        "NumeroGuiaOperadora": 26106190,
        "NumeroGuiaPrestador": GUIA_PRESTADOR,
        "Usuario": None,
        "DataHora": "/Date(1791301913000)/",
        "Observacao": "Prezados, Conforme ORIENTAÇÃO DA PORTARIA NORMATIVA nº 4/2023 - IPASGO/DAS-06148 e COMUNICADO enviado pelo IPASGO as documentações necessárias para análise são: <br>- Além da Solicitação Médica é necessário Relatório Médico relacionados ao acompanhamento do TEA emitidos por profissional médico regularmente habilitado, om validade de até 12 meses",
        "Login": "Auditoria IPASGO",
        "LiberaPendenteDoc": False,
        "LiberaAuditoriaOperadora": False,
        "LiberaAguardandoBeneficiario": False,
        "Id": 0,
    },
    {
        "NumeroGuiaOperadora": 26106190,
        "NumeroGuiaPrestador": GUIA_PRESTADOR,
        "Usuario": None,
        "DataHora": "/Date(1790823902000)/",
        "Observacao": "LEGENDA DOS ARQUIVOS ANEXOS: AI= AVALIAÇÃO INICIAL, RC = PTS MENSAL, RM = RELATORIO MÉDICO<br>Justificativa: AUTORIZAÇÃO MENSAL  OUTUBRO/2026 - Relativo aos atendimentos do período 01/10/2026 a 31/10/2026  anexo relatório médico e relatório Clínico, totalizando 14 Sessões Conforme PTS SEMANAL: 3X Semana, conforme agenda do paciente",
        "Login": "Auditoria IPASGO",
        "LiberaPendenteDoc": False,
        "LiberaAuditoriaOperadora": False,
        "LiberaAguardandoBeneficiario": False,
        "Id": 0,
    },
]


class TestBuildHistoricoParam:
    def test_bate_com_param_real_do_portal(self):
        assert build_historico_param(GUIA_OPERADORA, GUIA_PRESTADOR) == PARAM_BASE64

    def test_aceita_numeros(self):
        # OP11 pode carregar o NumeroGuiaOperadora como int (JSON do portal)
        assert build_historico_param(26106190, GUIA_PRESTADOR) == PARAM_BASE64


class TestParseAspnetDatetime:
    def test_timestamp_real(self):
        dt = parse_aspnet_datetime("/Date(1791314605000)/")
        assert dt is not None
        assert dt.isoformat() == "2026-10-06T16:23:25-03:00"

    def test_invalidos(self):
        assert parse_aspnet_datetime(None) is None
        assert parse_aspnet_datetime("") is None
        assert parse_aspnet_datetime("18/04/2026") is None


class TestNormalizarHistorico:
    def test_payload_real_ordenado_por_datahora_desc(self):
        itens = normalizar_historico(HISTORICO_CRU)
        assert len(itens) == 3
        datas = [i["data_hora"] for i in itens]
        assert datas == sorted(datas, reverse=True)
        assert datas[0] == "2026-10-06T16:23:25-03:00"

    def test_campos_mapeados(self):
        primeiro = normalizar_historico(HISTORICO_CRU)[0]
        assert primeiro["login"] == "Auditoria IPASGO"
        assert primeiro["usuario"] is None
        assert "PORTARIA NORMATIVA" in primeiro["observacao"]
        assert primeiro["libera_pendente_doc"] is False

    def test_entradas_nao_dict_sao_ignoradas(self):
        itens = normalizar_historico(["x", None, HISTORICO_CRU[0]])
        assert len(itens) == 1

    def test_payload_nao_lista(self):
        assert normalizar_historico(None) == []
        assert normalizar_historico({"HasError": True}) == []


# ── Persistência no sync (guia não autorizada vira solicitacao) ──

def _db_sem_solicitacao_existente():
    """Sessão mock: nenhuma solicitacao/base_guia pré-existente — fluxo de INSERT."""
    db = MagicMock()
    q1 = db.query.return_value.filter.return_value
    q2 = q1.filter.return_value
    # first() → None em cadeias de 1 ou 2 filters (lookups de existing/conflict)
    q1.first.return_value = None
    q2.first.return_value = None
    q1.all.return_value = []
    q2.all.return_value = []

    class _Nested:
        def commit(self):
            pass

        def rollback(self):
            pass

    db.begin_nested.return_value = _Nested()
    return db


class TestSyncPersisteHistoricoEGuiaPrestador:
    def test_insert_solicitacao_com_historico(self):
        from services.guias_sync_service import bulk_upsert_guias_from_json

        historico = normalizar_historico(HISTORICO_CRU)
        results = [{
            "numero_guia": GUIA_OPERADORA,
            "guia_prestador": GUIA_PRESTADOR,
            "status_guia": "Aguardando documentação",
            "codigo_terapia": "00040045",
            "historico_observacoes": historico,
        }]

        db = _db_sem_solicitacao_existente()
        bulk_upsert_guias_from_json(
            db=db, results=results, id_convenio=6, user_id=1,
            carteirinha_id=None, job_id=990,
        )

        criadas = [c for c in db.add.call_args_list
                   if type(c.args[0]).__name__ == "Solicitacao"]
        assert len(criadas) == 1
        sol = criadas[0].args[0]
        assert sol.historico_observacoes == historico
        assert sol.guia_prestador == GUIA_PRESTADOR
        assert sol.status_solicitacao == "Aguardando documentação"

    def test_guia_autorizada_nao_recebe_historico(self):
        from services.guias_sync_service import bulk_upsert_guias_from_json

        results = [{
            "numero_guia": "26106207",
            "guia_prestador": GUIA_PRESTADOR,
            "status_guia": "Autorizado",
            "codigo_terapia": "00040045",
            "historico_observacoes": normalizar_historico(HISTORICO_CRU),
        }]

        db = _db_sem_solicitacao_existente()
        bulk_upsert_guias_from_json(
            db=db, results=results, id_convenio=6, user_id=1,
            carteirinha_id=None, job_id=991,
        )

        criadas = [c for c in db.add.call_args_list
                   if type(c.args[0]).__name__ == "Solicitacao"]
        assert criadas == []  # autorizada vai para base_guias, não solicitacoes


# ── Aba Solicitações: Data Solicit. vem da coluna (OP11), não da importação ──

class TestSerializeSolicitacaoRow:
    def _make_row(self, **kw):
        from models import Solicitacao
        from sqlalchemy import inspect
        mapper = inspect(Solicitacao)
        base = {attr.key: None for attr in mapper.column_attrs}
        base.update(dict(
            id=123, user_id=1, id_convenio=6,
            guia=GUIA_OPERADORA,
            data_solicitacao=date(2026, 9, 30),
            data_autorizacao=None,
            status_solicitacao="Aguardando documentação",
            qtde_solicitada=14,
            sessoes_autorizadas=0,
            historico_observacoes=normalizar_historico(HISTORICO_CRU),
            guia_prestador=GUIA_PRESTADOR,
            created_at=datetime(2026, 10, 5, 12, 0, 0),
            updated_at=datetime(2026, 10, 5, 12, 0, 0),
        ))
        base.update(kw)
        return SimpleNamespace(**base)

    def test_data_solicitacao_da_coluna_e_nao_do_created_at(self):
        from routes.guias import _serialize_solicitacao_row

        row = self._make_row(data_solicitacao=date(2026, 9, 30),
                             created_at=datetime(2026, 10, 5, 12, 0, 0))
        s_dict = _serialize_solicitacao_row(row, "Paciente", "0063")
        assert s_dict["data_solicitacao"] == date(2026, 9, 30)

    def test_fallback_para_created_at_quando_portal_nao_informou(self):
        from routes.guias import _serialize_solicitacao_row

        row = self._make_row(data_solicitacao=None,
                             created_at=datetime(2026, 10, 5, 12, 0, 0))
        s_dict = _serialize_solicitacao_row(row, "Paciente", "0063")
        assert s_dict["data_solicitacao"] == date(2026, 10, 5)

    def test_historico_e_guia_prestador_serializados(self):
        from routes.guias import _serialize_solicitacao_row

        row = self._make_row()
        s_dict = _serialize_solicitacao_row(row, "Paciente", "0063")
        assert s_dict["historico_observacoes"][0]["login"] == "Auditoria IPASGO"
        assert s_dict["guia_prestador"] == GUIA_PRESTADOR
        assert s_dict["status_guia"] == "Aguardando documentação"
