"""
QA — OP1 Fature: cascata reg_ans × status_guia + fetch robusto (PO 2026-10-21).

Cobre a lógica pura do worker (sem Selenium/banco — padrão da casa, mesmo
modelo do test_ipasgo_module_reloader que importa módulo do Local_worker):
- ordem da cascata (status 5 primeiro; reg_ans do job antes do fallback; dedup);
- URL do GetGuiasLote igual ao exemplo real observado no portal (PO);
- classificação da resposta: 401 → auth (sessão expirada — causa-raiz auditada
  nos jobs 20614-20628: "Authentication failed" virava falso "não localizada"),
  0/429/5xx → transiente, 200+lista → ok, 200+não-lista → invalido;
- escada de recuperação: reciclagem do driver faz quit() SEM logoff e zera a ref.
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_WORKER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "Local_worker", "Worker"))
_OP1_PATH = os.path.join(_WORKER_ROOT, "1-bradesco", "op_fature", "op1_consultar_guias.py")
_SCRAPER_PATH = os.path.join(_WORKER_ROOT, "1-bradesco", "core", "scraper.py")


def _carregar_modulo_isolado(path, nome):
    """importlib do arquivo do worker SEM poluir o estado global (sys.modules/
    sys.path) — o pacote 'core'/'config' do 1-bradesco conflita com o 'core' do
    IPASGO no test_ipasgo_module_reloader quando a suíte roda junta."""
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
    """Carrega o op1 do worker via importlib (mesmo mecanismo do scraper.execute_op)."""
    return _carregar_modulo_isolado(_OP1_PATH, "op1_fature_cascata_test")


# ── Cascata (montar_tentativas) ─────────────────────────────────────────────

def test_cascata_prioriza_status_5_e_reg_ans_do_job(op1):
    tentativas = op1.montar_tentativas("5711")
    assert tentativas[0] == ("5711", 5)  # PO: começar por status_guia_id=5
    assert [t[0] for t in tentativas[:7]] == ["5711"] * 7  # todo o reg_ans do job primeiro
    assert ("421715", 5) in tentativas
    assert len(tentativas) == 14  # 2 reg_ans × 7 status


def test_cascata_dedup_quando_job_ja_usa_reg_ans_fallback(op1):
    tentativas = op1.montar_tentativas("421715")
    assert len(tentativas) == 7
    assert all(reg == "421715" for reg, _ in tentativas)


def test_cascata_ordem_completa_dos_status(op1):
    _, status_ = zip(*op1.montar_tentativas("5711"))
    assert list(status_) == [5, 101, 102, 103, 4, 3, 403, 5, 101, 102, 103, 4, 3, 403]


# ── URL do GetGuiasLote (exemplo real do PO) ────────────────────────────────

def test_url_igual_ao_exemplo_do_portal(op1):
    url = op1.montar_url_get_guias_lote("225529", "5711", 5, "1833964877")
    assert url == (
        "https://rest-guia-fature-apicast-production.api.ocppr.orizon.com.br/api/Status_Guia/GetGuiasLote"
        "?Prestador_Id=225529&Operadora_Id=48&Status_Guia_Id=5"
        "&carteirinhaOuNomeBeneficiario=1833964877&reg_ans=5711&numeroGuiaPrestador=1833964877"
    )


# ── Classificação da resposta ───────────────────────────────────────────────

def test_classificar_ok_com_itens_e_vazio(op1):
    assert op1.classificar_fetch(200, [{"StatusGuia": 5}]) == "ok_com_itens"
    assert op1.classificar_fetch(200, []) == "ok_vazio"


def test_classificar_auth_401_403(op1):
    """Auditoria 2026-10-21: 'Authentication failed' = sessão expirada, NUNCA vazio."""
    assert op1.classificar_fetch(401, None) == "auth"
    assert op1.classificar_fetch(403, None) == "auth"


def test_classificar_transiente_rede_429_5xx(op1):
    assert op1.classificar_fetch(0, None) == "transiente"
    assert op1.classificar_fetch(429, None) == "transiente"
    assert op1.classificar_fetch(500, None) == "transiente"


def test_classificar_invalido_200_nao_lista(op1):
    assert op1.classificar_fetch(200, {"erro": "x"}) == "invalido"
    assert op1.classificar_fetch(200, None) == "invalido"


# ── Escada de recuperação: reciclagem do driver ────────────────────────────

@pytest.fixture()
def scraper_cls(monkeypatch):
    """BradescoScraper sem __init__ (sem banco) — database/security mockados."""
    for nome in ("database", "security_utils", "server"):
        if nome not in sys.modules:
            fake = types.ModuleType(nome)
            fake.SessionLocal = MagicMock()
            fake.Base = MagicMock()  # models.py do worker importa Base daqui
            fake.decrypt_password = MagicMock(return_value="x")
            fake.sel_manager = MagicMock()  # process_job importa `from server import sel_manager`
            monkeypatch.setitem(sys.modules, nome, fake)
    monkeypatch.syspath_prepend(_WORKER_ROOT)
    mod = _carregar_modulo_isolado(_SCRAPER_PATH, "bradesco_scraper_test")
    return mod.BradescoScraper


def _scraper_para_teste(scraper_cls):
    """Instância mínima para exercitar process_job sem banco/browser."""
    from importlib.util import spec_from_file_location, module_from_spec
    exc_path = os.path.join(_WORKER_ROOT, "1-bradesco", "core", "exceptions.py")
    spec = spec_from_file_location("bradesco_exc_inline", exc_path)
    exc_mod = module_from_spec(spec)
    spec.loader.exec_module(exc_mod)

    s = scraper_cls.__new__(scraper_cls)
    s.db = MagicMock()
    s.log = MagicMock()
    s.driver = None
    s.username = "teste"
    s.password = "x"
    s.user_id = 14
    s.id_convenio = 1
    s.max_retries = 3
    s.headless = False
    s.module_path = os.path.join(_WORKER_ROOT, "1-bradesco")
    return s, exc_mod


def test_reciclar_driver_quit_sem_logoff_e_zera_ref(scraper_cls):
    """2ª queda consecutiva de sessão: quit() SEM logoff (nunca clica 'sair')."""
    scraper = scraper_cls.__new__(scraper_cls)
    drv = MagicMock()
    scraper.driver = drv
    scraper.log = MagicMock()
    scraper._reciclar_driver(job_id=1)
    drv.quit.assert_called_once()
    # SEM logoff: nenhum find_element/click de botão 'sair' além do quit
    drv.find_elements.assert_not_called()
    assert scraper.driver is None  # próximo attempt cria driver novo pelo pool


def test_process_job_propaga_erro_mfa(scraper_cls):
    """Incidente 20646: MFA_TIMEOUT terminava em job success vazio — o process_job
    agora levanta ValueError quando execution.status=error (server devolve error)."""
    import pytest as _pytest
    s, exc = _scraper_para_teste(scraper_cls)
    s.execute_op = MagicMock(side_effect=exc.MfaTimeoutError())
    with _pytest.raises(ValueError, match="MFA_TIMEOUT"):
        s.process_job("op0_login", {"job_id": 1})


def test_process_job_sucesso_nao_levanta(scraper_cls):
    """OP legítima que retorna [] continua success (sem raise)."""
    s, _ = _scraper_para_teste(scraper_cls)
    s.execute_op = MagicMock(return_value=[])
    result = s.process_job("op0_login", {"job_id": 1})
    assert result == []


def test_process_job_rotina_login_nao_executa_duplo(scraper_cls):
    """Incidente 20652: rotina op0_login executava 2x (self.login + execute_op) —
    a 2ª navega de volta ao portal já autenticado e explode no timeout."""
    s, _ = _scraper_para_teste(scraper_cls)
    s.login = MagicMock()
    s.execute_op = MagicMock(return_value=[{"status": "success"}])
    s.process_job("op0_login", {"job_id": 1})
    s.login.assert_not_called()
    assert s.execute_op.call_count == 1


def test_process_job_rotina_normal_faz_login_antes_da_op(scraper_cls):
    """Rotinas demais seguem o fluxo login → operação (comportamento preservado)."""
    s, _ = _scraper_para_teste(scraper_cls)
    s.login = MagicMock()
    s.execute_op = MagicMock(return_value=[])
    s.process_job("1", {"job_id": 1, "contexto": "fature"})
    s.login.assert_called_once()
    assert s.execute_op.call_count == 1
