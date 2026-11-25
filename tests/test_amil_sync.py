"""
QA — Sync AMIL (convênio 9): status normalizados + coluna nova do record
(plano docs/plano_amil_op0_op1.md, decisões D1/D2 revisada).

Cobre a lógica pura do guias_sync_service (padrão da suíte: sessão mockada,
sem rede/banco — mesmo modelo do test_bulk_upsert_dedup):
- _normalize_status dos status do portal AMIL (AUTORIZADO/CANCELADO/EM ANÁLISE,
  com e sem acento) sem afetar os demais convênios;
- montagem do record com a coluna nova status_token (pedidoConfirmado) e datas
  ISO do worker ('YYYY-MM-DD' e 'YYYY-MM-DDTHH:MM:SS');
- _apply_guia_update copiando a coluna nova (inclusive status_token=False);
- cadastro do vínculo codigo_procedimento → faturamento (pacote da API AMIL)
  na tabela de códigos public.procedimentos — sem coluna em base_guias;
- vínculo/criação da carteirinha com o nome do beneficiário
  (numeroAssociado/nomeAssociado — plano AMIL §3).
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import models as m
from services.guias_sync_service import (
    _apply_guia_update,
    _cadastrar_codigos_faturamento,
    _ensure_carteirinha_amil,
    _normalize_status,
    _parse_date,
    bulk_upsert_guias_from_json,
)


ITEM_AMIL = {
    "numero_guia": "538503088",
    "status": "AUTORIZADO",
    "codigo_terapia": "10101012",
    "nome_terapia": "SESSAO DE FISIOTERAPIA",
    "senha": "1234567890",
    "carteirinha": "000123456789",       # numeroAssociado
    "paciente": "JOSE DA SILVA",         # nomeAssociado
    "codigo_beneficiario": "000123456789",
    "qtde_solicitada": 10,
    "qtde_autorizada": 8,
    "status_token": True,                # pedidoConfirmado (detalhe da guia)
    "codigo_faturamento": "PCT001",      # pacotesAutorizados[0].codigo
    "data_solicitacao": "2026-11-01",
    "data_autorizacao": "2026-11-02T14:30:00",
}


# ── _normalize_status (situacao AMIL) ───────────────────────────────────────

class TestNormalizeStatusAmil:

    def test_autorizado(self):
        assert _normalize_status("AUTORIZADO", 9, ITEM_AMIL) == "Autorizado"

    def test_cancelado(self):
        assert _normalize_status("CANCELADO", 9, ITEM_AMIL) == "Cancelado"

    def test_em_analise_com_e_sem_acento(self):
        assert _normalize_status("EM ANALISE", 9, ITEM_AMIL) == "Em análise"
        assert _normalize_status("EM ANÁLISE", 9, ITEM_AMIL) == "Em análise"

    def test_analise_simples_com_e_sem_acento(self):
        assert _normalize_status("ANALISE", 9, ITEM_AMIL) == "Em análise"
        assert _normalize_status("ANÁLISE", 9, ITEM_AMIL) == "Em análise"

    def test_maiusculas_minusculas_mistas(self):
        assert _normalize_status("Autorizado", 9, ITEM_AMIL) == "Autorizado"
        assert _normalize_status("Em Analise", 9, ITEM_AMIL) == "Em análise"

    def test_outros_convenios_nao_afetados(self):
        # Sem mapeamento AMIL: status desconhecido segue cru (comportamento antigo)
        assert _normalize_status("CANCELADO", 6, {}) == "CANCELADO"


# ── Datas ISO do worker AMIL ────────────────────────────────────────────────

class TestDatasIsoAmil:

    def test_somente_data(self):
        assert _parse_date("2026-11-20") == date(2026, 11, 20)

    def test_data_com_hora(self):
        assert _parse_date("2026-11-20T14:30:00") == date(2026, 11, 20)


# ── Record com as colunas novas (bulk_upsert insert) ────────────────────────

def _db_mock(carteirinha=None, base_guia=None, procedimento=None):
    """Sessão mock com filas configuradas por model (padrão da suíte)."""
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is m.Carteirinha:
            q.filter.return_value.all.return_value = []
            q.filter.return_value.first.return_value = carteirinha
        elif model is m.BaseGuia:
            q.filter.return_value.all.return_value = [base_guia] if base_guia else []
            q.filter.return_value.first.return_value = base_guia
        elif model is m.Procedimento:
            q.filter.return_value.first.return_value = procedimento
        elif model is m.Solicitacao:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    db.query.side_effect = _query
    db.begin_nested.return_value = MagicMock()
    return db


class TestRecordColunasNovas:

    def test_insert_amil_cria_guia_com_status_token_e_cadastra_codigo(self):
        cart = SimpleNamespace(id=501, paciente=None, carteirinha=None,
                               codigo_beneficiario="000123456789")
        db = _db_mock(carteirinha=cart)

        res = bulk_upsert_guias_from_json(db, [ITEM_AMIL], id_convenio=9, user_id=1)

        assert res["inserted"] == 1
        # Carteirinha existente ganha o nome do beneficiário (numeroAssociado)
        assert cart.paciente == "JOSE DA SILVA"

        guia = next(c[0][0] for c in db.add.call_args_list if isinstance(c[0][0], m.BaseGuia))
        assert guia.status_guia == "Autorizado"
        assert guia.status_token is True
        assert guia.carteirinha_id == 501
        assert guia.data_solicitacao == date(2026, 11, 1)
        assert guia.data_autorizacao == date(2026, 11, 2)  # ISO com hora aceito
        # Pacote de faturamento NÃO é coluna de base_guias (D2 revisada)
        assert not hasattr(guia, "codigo_faturamento")

        # Vínculo proc→pacote cadastrado na tabela de códigos (procedimentos)
        proc = next(o for o in (c[0][0] for c in db.add.call_args_list)
                    if isinstance(o, m.Procedimento))
        assert proc.codigo_procedimento == "10101012"
        assert proc.faturamento == "PCT001"
        assert proc.id_convenio == 9
        assert proc.status == "ativo"

    def test_status_cancelado_vai_para_solicitacoes(self):
        cart = SimpleNamespace(id=501, paciente=None, carteirinha=None,
                               codigo_beneficiario="000123456789")
        db = _db_mock(carteirinha=cart)
        item = dict(ITEM_AMIL, status="CANCELADO")

        res = bulk_upsert_guias_from_json(db, [item], id_convenio=9, user_id=1)

        assert res["inserted"] == 0  # nada em base_guias
        adicionadas = [c[0][0] for c in db.add.call_args_list]
        assert not any(isinstance(o, m.BaseGuia) for o in adicionadas)
        sol = next(o for o in adicionadas if isinstance(o, m.Solicitacao))
        assert sol.status_solicitacao == "Cancelado"
        assert sol.carteirinha_id == 501


class TestApplyGuiaUpdateColunasNovas:

    def _linha(self, **kw):
        base = dict(status_token=None, senha=None,
                    status_guia="Antigo", user_id=1)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_copia_status_token(self):
        linha = self._linha()
        _apply_guia_update(linha, {"status_token": True}, user_id=1)
        assert linha.status_token is True

    def test_status_token_false_e_aplicado(self):
        # False é valor válido (pedidoConfirmado=false) e não pode ser pulado
        linha = self._linha(status_token=True)
        _apply_guia_update(linha, {"status_token": False}, user_id=1)
        assert linha.status_token is False

    def test_none_nao_sobrescreve_existente(self):
        linha = self._linha(status_token=True)
        _apply_guia_update(linha, {"status_token": None}, user_id=1)
        assert linha.status_token is True


# ── Cadastro de códigos em procedimentos (D2 revisada) ───────────────────────

class TestCadastrarCodigosFaturamento:

    def test_cria_procedimento_quando_nao_existe(self):
        db = _db_mock()
        garantidos = _cadastrar_codigos_faturamento(
            db,
            [{"codigo_procedimento": "10101012", "nome": "SESSAO DE FISIOTERAPIA",
              "faturamento": "PCT001"}],
            id_convenio=9,
        )
        assert garantidos == 1
        proc = db.add.call_args[0][0]
        assert isinstance(proc, m.Procedimento)
        assert proc.codigo_procedimento == "10101012"
        assert proc.faturamento == "PCT001"
        assert proc.nome == "SESSAO DE FISIOTERAPIA"
        assert proc.id_convenio == 9
        assert proc.status == "ativo"

    def test_procedimento_existente_ganha_faturamento_e_nome(self):
        proc = SimpleNamespace(codigo_procedimento="10101012", faturamento=None,
                               nome=None)
        db = _db_mock(procedimento=proc)
        garantidos = _cadastrar_codigos_faturamento(
            db,
            [{"codigo_procedimento": "10101012", "nome": "SESSAO DE FISIOTERAPIA",
              "faturamento": "PCT001"}],
            id_convenio=9,
        )
        assert garantidos == 1
        assert proc.faturamento == "PCT001"
        assert proc.nome == "SESSAO DE FISIOTERAPIA"
        db.add.assert_not_called()

    def test_faturamento_diferente_atualiza_igual_preserva(self):
        proc = SimpleNamespace(codigo_procedimento="10101012", faturamento="PCT001",
                               nome="Já cadastrado")
        db = _db_mock(procedimento=proc)
        _cadastrar_codigos_faturamento(
            db, [{"codigo_procedimento": "10101012", "faturamento": "PCT001"}],
            id_convenio=9,
        )
        # Mesmo vínculo já gravado: nada muda, nome preservado
        assert proc.faturamento == "PCT001"
        assert proc.nome == "Já cadastrado"

        proc.faturamento = "PCT_ANTIGO"
        garantidos = _cadastrar_codigos_faturamento(
            db, [{"codigo_procedimento": "10101012", "faturamento": "PCT001"}],
            id_convenio=9,
        )
        assert garantidos == 1
        assert proc.faturamento == "PCT001"

    def test_dedup_por_codigo_procedimento(self):
        db = _db_mock()
        garantidos = _cadastrar_codigos_faturamento(
            db,
            [{"codigo_procedimento": "10101012", "faturamento": "PCT001"},
             {"codigo_procedimento": "10101012", "faturamento": "PCT001"}],
            id_convenio=9,
        )
        assert garantidos == 1
        db.add.assert_called_once()

    def test_pares_incompletos_ignorados(self):
        db = _db_mock()
        assert _cadastrar_codigos_faturamento(
            db,
            [{"codigo_procedimento": "", "faturamento": "PCT001"},
             {"codigo_procedimento": "10101012", "faturamento": ""}],
            id_convenio=9,
        ) == 0
        db.query.assert_not_called()
        db.add.assert_not_called()


# ── Carteirinha do beneficiário (numeroAssociado/nomeAssociado) ──────────────

class TestEnsureCarteirinhaAmil:

    def test_carteirinha_existente_ganha_nome_e_numero(self):
        cart = SimpleNamespace(id=77, paciente=None, carteirinha=None,
                               codigo_beneficiario="000123456789")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = cart

        assert _ensure_carteirinha_amil(db, ITEM_AMIL, user_id=1) == 77
        assert cart.paciente == "JOSE DA SILVA"
        assert cart.carteirinha == "000123456789"
        db.add.assert_not_called()

    def test_cria_carteirinha_quando_nao_existe(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        db.begin_nested.return_value = MagicMock()

        def _flush_atribui_id():
            nova = db.add.call_args[0][0]
            nova.id = 4242  # flush real atribuiria o id da sequência
        db.flush.side_effect = _flush_atribui_id

        assert _ensure_carteirinha_amil(db, ITEM_AMIL, user_id=1) == 4242
        nova = db.add.call_args[0][0]
        assert isinstance(nova, m.Carteirinha)
        assert nova.carteirinha == "000123456789"
        assert nova.paciente == "JOSE DA SILVA"
        assert nova.codigo_beneficiario == "000123456789"
        assert nova.id_convenio == 9
        assert nova.user_id == 1
        assert nova.status == "ativo"

    def test_sem_identificadores_retorna_none(self):
        db = MagicMock()
        assert _ensure_carteirinha_amil(db, {"paciente": "SEM ID"}, user_id=1) is None
        db.query.assert_not_called()
