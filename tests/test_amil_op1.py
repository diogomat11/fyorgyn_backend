"""
QA — OP1 AMIL: filtros D6 (revisada), paginação D7 e mapeamento de records.

Cobre a lógica pura do worker (sem Selenium/banco — padrão da casa, mesmo
modelo do test_fature_cascata que importa módulo do Local_worker via
importlib/spec_from_file_location):
- montar_params_busca: contrato REAL do portal (DevTools 2026-11-20) — filtro
  unificado `identificacao` (senha > guia > carteirinha) SEM datas; datas
  obrigatórias como último recurso; normalização DD/MM→YYYY-MM-DD;
  sempre codigoCredenciado/limit/sort;
- calcular_starts: [1, 11, 21...] conforme D7 (start=1, incrementa limit),
  0 → [], safety cap MAX_PAGINAS;
- montar_url_lista / montar_url_detalhe (D9);
- mapear_pedido_para_records: 1 record por procedimento do detalhe; sem
  detalhe → record mínimo; pedidoConfirmado→status_token; pacote→
  codigo_faturamento; datas truncadas [:10]; validade do detalhe prioritária;
- _eh_404_vazio: 404 'integracao.pedido.autorizacao.nao.encontrado' = vazio;
- cascata de tokens (core/session_extractor — D4): fonte primária
  localStorage['acesso-info'] (tokenInfo.accessToken/sessionToken/
  credenciadoInfo.codigo) + fallback decode JWT sem verificação.
"""
import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_WORKER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "Local_worker", "Worker"))
_OP1_PATH = os.path.join(_WORKER_ROOT, "9-amil", "op", "op1_consulta_guias.py")
_SESSION_EXTRACTOR_PATH = os.path.join(_WORKER_ROOT, "9-amil", "core", "session_extractor.py")
_CONSTANTS_PATH = os.path.join(_WORKER_ROOT, "9-amil", "config", "constants.py")


def _carregar_modulo_isolado(path, nome):
    """importlib do arquivo do worker SEM poluir o estado global (sys.modules/
    sys.path) — o pacote 'core'/'config' do 9-amil conflita com o 'core' de
    outros integradores quando a suíte roda junta (padrão test_fature_cascata)."""
    import importlib.util
    mods_antes = set(sys.modules)
    paths_antes = list(sys.path)
    spec = importlib.util.spec_from_file_location(nome, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in set(sys.modules) - mods_antes:
        if k != nome:
            sys.modules.pop(k, None)
    sys.path[:] = paths_antes
    return mod


@pytest.fixture(scope="module")
def op1():
    """Carrega a OP1 do worker via importlib (mesmo mecanismo do scraper.execute_op)."""
    return _carregar_modulo_isolado(_OP1_PATH, "op1_amil_test")


@pytest.fixture(scope="module")
def constants():
    return _carregar_modulo_isolado(_CONSTANTS_PATH, "amil_constants_test")


@pytest.fixture(scope="module")
def session_extractor():
    return _carregar_modulo_isolado(_SESSION_EXTRACTOR_PATH, "amil_session_extractor_test")


# ── montar_params_busca (decisão D6 revisada — contrato real do portal) ──────

def test_params_senha_tem_precedencia_e_dispensa_datas(op1):
    """D6: senha informada → identificacao SEM datas (mesmo que venham no job).
    Contrato real: filtro unificado `identificacao` (senha/carteirinha/guia) e
    a API descarta datas quando há identificador."""
    query = op1.montar_params_busca(
        {"senha": "123456789", "carteirinha": "9999", "data_ini": "2026-01-01", "data_fim": "2026-01-31"},
        "67628788",
    )
    assert query["identificacao"] == "123456789"
    assert "dataAutorizacaoDe" not in query and "dataAutorizacaoAte" not in query
    assert "senhaAutorizacao" not in query and "numeroAssociado" not in query


def test_params_senha_alias_senhaAutorizacao(op1):
    query = op1.montar_params_busca({"senhaAutorizacao": "ABC123"}, "67628788")
    assert query["identificacao"] == "ABC123"


def test_params_guia_vira_identificador(op1):
    """guia (numeroPedido) também entra no filtro unificado (sem datas)."""
    query = op1.montar_params_busca({"guia": "538503088"}, "67628788")
    assert query["identificacao"] == "538503088"
    assert "dataAutorizacaoDe" not in query


def test_params_carteirinha_sem_datas(op1):
    """Contrato real: com identificador a API descarta datas (o SPA não envia)."""
    query = op1.montar_params_busca(
        {"carteirinha": "7011000123", "data_ini": "2026-01-01", "data_fim": "2026-01-31"},
        "67628788",
    )
    assert query["identificacao"] == "7011000123"
    assert "dataAutorizacaoDe" not in query and "dataAutorizacaoAte" not in query
    assert "senhaAutorizacao" not in query and "numeroAssociado" not in query


def test_params_apenas_datas_obrigatorias(op1):
    query = op1.montar_params_busca({"data_ini": "2026-01-01", "data_fim": "2026-01-31"}, "67628788")
    assert query["dataAutorizacaoDe"] == "2026-01-01"
    assert query["dataAutorizacaoAte"] == "2026-01-31"
    assert "identificacao" not in query


def test_params_normaliza_data_barrada(op1):
    """DD/MM/YYYY → YYYY-MM-DD (mesma normalização do padrão bradesco)."""
    query = op1.montar_params_busca(
        {"data_ini": "01/02/2026", "data_fim": "28/02/2026"},
        "67628788",
    )
    assert query["dataAutorizacaoDe"] == "2026-02-01"
    assert query["dataAutorizacaoAte"] == "2026-02-28"


def test_params_base_fixa_codigo_limit_sort(op1):
    query = op1.montar_params_busca({"senha": "X"}, "67628788")
    assert query["codigoCredenciado"] == "67628788"
    assert query["limit"] == op1.PAGE_LIMIT == 10
    assert query["sort"] == "-numeroPedido"


def test_params_sem_filtro_erro(op1):
    with pytest.raises(ValueError, match="AMIL_OP1_SEM_FILTRO"):
        op1.montar_params_busca({"data_ini": "2026-01-01"}, "67628788")  # só data_ini não basta


def test_params_sem_prestador_erro(op1):
    with pytest.raises(ValueError, match="AMIL_OP1_SEM_PRESTADOR"):
        op1.montar_params_busca({"senha": "X"}, "")


# ── calcular_starts (decisão D7) ────────────────────────────────────────────

def test_starts_total_140_gera_14_paginas_de_1_a_131(op1):
    """totalCount=140, limit=10 → 14 páginas: start 1, 11, ..., 131
    (observado no portal: page1 start=1, next=11; D7 determinístico)."""
    starts = op1.calcular_starts(140, 10)
    assert starts == [1 + 10 * i for i in range(14)]
    assert starts[0] == 1 and starts[-1] == 131 and len(starts) == 14


def test_starts_zero_ou_negativo_lista_vazia(op1):
    assert op1.calcular_starts(0, 10) == []
    assert op1.calcular_starts(-5, 10) == []


def test_starts_total_exato_multiplo(op1):
    """100/10 → 10 páginas (1..91); sem página fantasma além do total."""
    starts = op1.calcular_starts(100, 10)
    assert len(starts) == 10 and starts[-1] == 91


def test_starts_respeita_cap_max_paginas(op1, constants):
    """Safety cap: total gigante não estoura MAX_PAGINAS (500)."""
    starts = op1.calcular_starts(100_000, 10)
    assert len(starts) == constants.MAX_PAGINAS == 500


# ── URLs ────────────────────────────────────────────────────────────────────

def test_url_lista_querystring(op1):
    url = op1.montar_url_lista(
        {"codigoCredenciado": "67628788", "limit": 10, "sort": "-numeroPedido", "start": 11}
    )
    assert url == (
        "https://credenciado.amil.com.br/credenciadomedico/pedidos-autorizacao"
        "?codigoCredenciado=67628788&limit=10&sort=-numeroPedido&start=11"
    )


def test_url_detalhe_d9(op1):
    url = op1.montar_url_detalhe("123456", "7011000123")
    assert url == (
        "https://credenciado.amil.com.br/credenciadomedico/pedidos-autorizacao"
        "/123456?numeroAssociado=7011000123"
    )


# ── mapear_pedido_para_records (contrato guias_sync_service) ───────────────

def _pedido_exemplo():
    return {
        "numeroPedido": "123456",
        "numeroAssociado": "7011000123",
        "nomeAssociado": "MARIA DA SILVA",
        "situacao": "AUTORIZADO",
        "senhaAutorizacao": "9999",
        "dataSolicitacao": "2026-01-10T00:00:00",
        "dataAutorizacao": "2026-01-12T14:30:00",
        "dataValidade": "2026-07-12T00:00:00",
    }


def _detalhe_exemplo():
    return {
        "numeroGuia": "538503088seq001",
        "pedidoConfirmado": True,
        "validadeSenhaAutorizacao": "2026-10-01T00:00:00",
        # pacotesAutorizados vive DENTRO de sequenciaPrincipal (contrato real —
        # incidente job 28845: leitura na raiz deixava codigo_faturamento nulo)
        "sequenciaPrincipal": {
            "pacotesAutorizados": [{"codigo": "PCT01"}, {"codigo": "PCT02"}],
            "procedimentosMedicos": [
                {"codigo": "10101012", "nome": "SESSAO PSICOTERAPIA", "quantidadeSolicitada": 12, "quantidadeAutorizada": 10},
                {"codigo": "10101035", "nome": "SESSAO FISIOTERAPIA", "quantidadeSolicitada": 4, "quantidadeAutorizada": 4},
            ]
        },
    }


def test_mapear_com_detalhe_um_record_por_procedimento(op1):
    records = op1.mapear_pedido_para_records(_pedido_exemplo(), _detalhe_exemplo())
    assert len(records) == 2
    r0, r1 = records
    # Campos comuns (vêm da listagem + detalhe)
    for r in (r0, r1):
        assert r["guia"] == "123456"
        assert r["guia_prestador"] == "538503088seq001"
        assert r["carteirinha"] == "7011000123"
        assert r["codigo_beneficiario"] == "7011000123"
        assert r["paciente"] == "MARIA DA SILVA"
        assert r["status_guia"] == "AUTORIZADO"
        assert r["senha"] == "9999"
        assert r["data_solicitacao"] == "2026-01-10"   # [:10]
        assert r["data_autorizacao"] == "2026-01-12"   # [:10]
        assert r["validade"] == "2026-10-01"           # validadeSenhaAutorizacao do detalhe > dataValidade
        assert r["status_token"] is True               # pedidoConfirmado
        assert r["codigo_faturamento"] == "PCT01"      # pacotesAutorizados[0].codigo
    # Campos por procedimento
    assert (r0["codigo_terapia"], r0["nome_terapia"], r0["qtde_solicitada"], r0["sessoes_autorizadas"]) == \
        ("10101012", "SESSAO PSICOTERAPIA", 12, 10)
    assert (r1["codigo_terapia"], r1["nome_terapia"], r1["qtde_solicitada"], r1["sessoes_autorizadas"]) == \
        ("10101035", "SESSAO FISIOTERAPIA", 4, 4)


def test_mapear_sem_detalhe_record_minimo(op1):
    records = op1.mapear_pedido_para_records(_pedido_exemplo(), None)
    assert len(records) == 1
    r = records[0]
    assert r["guia"] == "123456"
    assert r["guia_prestador"] is None
    assert r["status_token"] is None
    assert r["codigo_faturamento"] is None
    assert r["codigo_terapia"] is None and r["nome_terapia"] is None
    assert r["validade"] == "2026-07-12"  # fallback dataValidade da listagem


def test_mapear_detalhe_sem_procedimentos_record_minimo(op1):
    detalhe = {"numeroGuia": "G1", "pedidoConfirmado": False, "sequenciaPrincipal": {"procedimentosMedicos": []}}
    records = op1.mapear_pedido_para_records(_pedido_exemplo(), detalhe)
    assert len(records) == 1
    assert records[0]["guia_prestador"] == "G1"
    assert records[0]["status_token"] is False


def test_mapear_validade_fallback_quando_detalhe_sem_validade_senha(op1):
    detalhe = {"sequenciaPrincipal": {"procedimentosMedicos": [{"codigo": "C1", "nome": "N", "quantidadeSolicitada": 1, "quantidadeAutorizada": 1}]}}
    records = op1.mapear_pedido_para_records(_pedido_exemplo(), detalhe)
    assert records[0]["validade"] == "2026-07-12"  # cai no dataValidade do pedido


def test_mapear_pedido_confirmado_false_e_sem_pacotes(op1):
    detalhe = {
        "numeroGuia": "G2",
        "pedidoConfirmado": False,
        "sequenciaPrincipal": {"procedimentosMedicos": [{"codigo": "C1", "nome": "N1", "quantidadeSolicitada": 3, "quantidadeAutorizada": 2}]},
    }
    records = op1.mapear_pedido_para_records(_pedido_exemplo(), detalhe)
    assert records[0]["status_token"] is False
    assert records[0]["codigo_faturamento"] is None  # pacotesAutorizados ausente


# ── Bônus: cascata de tokens (core/session_extractor — D4) ─────────────────

def _jwt_payload(payload: dict) -> str:
    def b64(seg: bytes) -> str:
        return base64.urlsafe_b64encode(seg).decode("ascii").rstrip("=")
    return f"{b64(b'header')}.{b64(json.dumps(payload).encode())}.{b64(b'signature')}"


def test_decodificar_jwt_payload_sem_verificacao(session_extractor):
    token = _jwt_payload({"accessToken": "SECRETACCESS", "sub": "67628788"})
    payload = session_extractor.decodificar_jwt_payload(token)
    assert payload["accessToken"] == "SECRETACCESS"
    assert session_extractor.decodificar_jwt_payload("nao-e-um-jwt") is None
    assert session_extractor.decodificar_jwt_payload("a.b") is None


def test_selecionar_tokens_prioriza_jwt_com_access_token(session_extractor):
    x_session = _jwt_payload({"accessToken": "SECRETACCESS"})
    achados = {
        "local:qualquer_coisa_amil": "texto simples",
        "session:x-session-token": x_session,
    }
    sel = session_extractor.selecionar_tokens(achados)
    assert sel["x_session"] == x_session
    assert sel["authorization"] == "Bearer SECRETACCESS"


def test_selecionar_tokens_sem_access_token_deixa_authorization_none(session_extractor):
    x_session = _jwt_payload({"sub": "1"})
    sel = session_extractor.selecionar_tokens({"local:amil-session": x_session})
    assert sel["x_session"] == x_session
    assert sel["authorization"] is None


def test_selecionar_tokens_limpa_aspas_de_storage(session_extractor):
    x_session = _jwt_payload({"accessToken": "TOK"})
    sel = session_extractor.selecionar_tokens({"local:token": json.dumps(x_session)})
    assert sel["authorization"] == "Bearer TOK"


# ── Fonte primária: acesso-info (contrato real do portal — DevTools 2026-11-20)

def test_acesso_info_fonte_primaria_completa(session_extractor):
    info = {
        "tokenInfo": {"accessToken": "YWJjZGVmZ2g", "expiresIn": 1800},
        "sessionToken": _jwt_payload({"codigoAmil": "67628788"}),
        "credenciadoInfo": {"codigo": "67628788", "nome": "CLINICA"},
        "usuarioInfo": {"cpf": "02378954557"},
    }
    sel = session_extractor.selecionar_tokens_de_acesso_info(info)
    assert sel["authorization"] == "Bearer YWJjZGVmZ2g"
    assert sel["x_session"] == info["sessionToken"]
    assert sel["x_user"] == "67628788"


def test_acesso_info_shape_inesperado_devolve_none(session_extractor):
    # Qualquer desvio de shape devolve None por campo (fallback decide)
    sel = session_extractor.selecionar_tokens_de_acesso_info({"tokenInfo": "corrompido"})
    assert sel == {"authorization": None, "x_session": None, "x_user": None}
    assert session_extractor.selecionar_tokens_de_acesso_info(None)["authorization"] is None


def test_404_vazio_e_contrato_da_listagem(op1):
    """404 com messageKey de 'nenhum pedido encontrado' = resultado VAZIO
    (não é erro — sem retry; diagnóstico DevTools 2026-11-20)."""
    corpo = json.dumps({
        "messageKey": op1.KEY_MSG_404_VAZIO,
        "defaultMessage": "Nenhum pedido autorização encontrado para os parametros informados.",
    })
    assert op1._eh_404_vazio(404, corpo) is True
    assert op1._eh_404_vazio(404, "texto-aleatorio") is False
    assert op1._eh_404_vazio(500, corpo) is False


# ── Processamento em lote (perf jobs 28845/28846) ────────────────────────────

def test_dividir_em_lotes(op1):
    itens = list(range(199))
    lotes = op1.dividir_em_lotes(itens, 24)
    assert len(lotes) == 9                       # ceil(199/24)
    assert sum(len(l) for l in lotes) == 199     # sem perda
    assert all(len(l) == 24 for l in lotes[:-1]) # lotes cheios
    assert len(lotes[-1]) == 199 - 24 * 8        # último menor (7)

    assert op1.dividir_em_lotes([], 24) == []
    assert op1.dividir_em_lotes([1], 24) == [[1]]
    assert op1.dividir_em_lotes([1, 2, 3], 0) == [[1, 2, 3]]  # defesa


# ── Scraper (core/scraper.py — mapa de rotinas e escada de erros) ──────────

_SCRAPER_PATH = os.path.join(_WORKER_ROOT, "9-amil", "core", "scraper.py")


@pytest.fixture(scope="module")
def amil_scraper_cls():
    """AmilScraper sem __init__ (sem banco) — database/security mockados.

    Base do mock é um declarative_base REAL (MagicMock quebra a definição das
    classes de models no Python 3.14 — Column/relationship deixam de existir)."""
    from sqlalchemy.orm import declarative_base
    mods_antes = set(sys.modules)
    paths_antes = list(sys.path)
    # Os módulos reais JÁ ESTÃO em sys.modules (importados por outros testes):
    # guardar para restaurar — substituir direto e depender do diff de chaves
    # vazaria os fakes (sem get_db) quebrando `from database import get_db`
    # nos testes seguintes.
    originais = {nome: sys.modules.get(nome)
                 for nome in ("database", "security_utils", "server")}
    for nome in ("database", "security_utils", "server"):
        fake = types.ModuleType(nome)
        fake.SessionLocal = MagicMock()
        fake.Base = declarative_base()
        fake.decrypt_password = MagicMock(return_value="x")
        fake.sel_manager = MagicMock()
        sys.modules[nome] = fake
    try:
        mod = _carregar_modulo_isolado(_SCRAPER_PATH, "amil_scraper_test")
        return mod.AmilScraper
    finally:
        for nome, mod in originais.items():
            if mod is not None:
                sys.modules[nome] = mod
            else:
                sys.modules.pop(nome, None)
        for k in set(sys.modules) - mods_antes:
            if k != "amil_scraper_test":
                sys.modules.pop(k, None)
        sys.path[:] = paths_antes


def _scraper_para_teste(amil_scraper_cls, first_convenio=None):
    """Instância mínima para exercitar process_job sem banco/browser."""
    s = amil_scraper_cls.__new__(amil_scraper_cls)
    s.db = MagicMock()
    s.db.query.return_value.filter.return_value.first.return_value = first_convenio
    s.log = MagicMock()
    s.driver = None
    s.username = "teste"
    s.password = "x"
    s.user_id = 1
    s.id_convenio = 9
    s.max_retries = 3
    s.headless = False
    s.module_path = os.path.join(_WORKER_ROOT, "9-amil")
    s.cod_prestador = "67628788"
    return s


_JOB_CRED = {"job_id": 1, "login": "teste", "senha_criptografada": "abc", "cod_prestador": "67628788"}


def test_rotina_op0_login_nao_executa_duplo(amil_scraper_cls):
    """Rotina de login é auto-suficiente — self.login + execute_op não podem
    rodar a OP0 duas vezes (lição do incidente Bradesco 20652)."""
    s = _scraper_para_teste(amil_scraper_cls)
    s.login = MagicMock()
    s.execute_op = MagicMock(return_value=[{"login_ok": True}])
    s.process_job("op0_login", dict(_JOB_CRED))
    s.login.assert_not_called()
    assert s.execute_op.call_count == 1
    assert s.execute_op.call_args[0][0] == "op0_login"


def test_rotina_1_mapeia_para_op1_e_faz_login_antes(amil_scraper_cls):
    """Rotinas canônicas/numéricas (D5): '1' → op1_consulta_guias, com login
    antes da operação."""
    s = _scraper_para_teste(amil_scraper_cls)
    s.login = MagicMock()
    s.execute_op = MagicMock(return_value=[])
    s.process_job("1", dict(_JOB_CRED))
    s.login.assert_called_once()
    assert s.execute_op.call_count == 1
    assert s.execute_op.call_args[0][0] == "op1_consulta_guias"


def test_rotina_alias_op1_consulta(amil_scraper_cls):
    """Alias canônico do seed (NovaSolicitacaoModal.rotinaMap[9]='op1_consulta')."""
    s = _scraper_para_teste(amil_scraper_cls)
    s.login = MagicMock()
    s.execute_op = MagicMock(return_value=[])
    s.process_job("op1_consulta", dict(_JOB_CRED))
    assert s.execute_op.call_args[0][0] == "op1_consulta_guias"


def test_erro_login_rejeitado_sem_retry(amil_scraper_cls):
    """Prefixo LOGIN_REJEITADO é FATAL (imune à purga de sys.modules que quebra
    isinstance): 1 tentativa apenas e o erro propaga (job error, não success vazio)."""
    s = _scraper_para_teste(amil_scraper_cls)
    s.execute_op = MagicMock(side_effect=ValueError("LOGIN_REJEITADO: usuário ou senha inválidos"))
    with pytest.raises(ValueError, match="LOGIN_REJEITADO"):
        s.process_job("op1_consulta", dict(_JOB_CRED))
    assert s.execute_op.call_count == 1  # sem retry — protege a conta


def test_erro_sessao_expirada_prefixo_ativa_escada(amil_scraper_cls):
    """Prefixo SESSAO_EXPIRADA: escada de recuperação — re-login a cada queda,
    reciclagem do driver na 2ª consecutiva, error apenas esgotando as tentativas."""
    s = _scraper_para_teste(amil_scraper_cls)
    s.execute_op = MagicMock(side_effect=ValueError("SESSAO_EXPIRADA: HTTP 401 na API"))
    s._reciclar_driver = MagicMock()
    with pytest.raises(ValueError, match="SESSAO_EXPIRADA"):
        s.process_job("op1_consulta", dict(_JOB_CRED))
    assert s.execute_op.call_count == s.max_retries  # tentou com re-login
    # Recicla a partir da 2ª queda CONSECUTIVA e mantém reciclando enquanto
    # persistir (>= 2, padrão bradesco): quedas = max_retries → reciclagens = max_retries - 1
    assert s._reciclar_driver.call_count == s.max_retries - 1


def test_sem_credenciais_erro_fatal_sem_tentativa(amil_scraper_cls):
    """Params sem credenciais e user_convenios vazio → AMIL_SEM_CREDENCIAIS
    fatal: nenhuma execução de OP (sem risco de bloqueio no portal)."""
    s = _scraper_para_teste(amil_scraper_cls)
    s.execute_op = MagicMock()
    with pytest.raises(ValueError, match="AMIL_SEM_CREDENCIAIS"):
        s.process_job("op1_consulta", {"job_id": 1})
    s.execute_op.assert_not_called()
