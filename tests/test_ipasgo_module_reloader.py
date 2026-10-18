"""
QA — Hot-reload de módulos core do integrador (incidente job #19062).

Cobrem:
- módulo do integrador alterado em disco (mtime futuro) é recarregado e a nova
  versão passa a valer (causa-raiz do AttributeError
  'WebPlanClient' object has no attribute 'get_historico_observacao');
- módulo sem alteração NÃO é recarregado;
- módulos fora da raiz do integrador são ignorados.
"""
import os
import sys
import textwrap

import pytest

_CORE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..",
                 "Local_worker", "Worker", "6-ipasgo", "core")
)
sys.path.insert(0, _CORE_DIR)

from historico_observacoes import build_historico_param  # noqa: F401,E402 (aquece o core)
from module_reloader import reload_stale_modules  # noqa: E402


@pytest.fixture()
def integrador_tmp(tmp_path, monkeypatch):
    """Raiz de integrador falsa com um módulo core dummy já importado."""
    (tmp_path / "core").mkdir()
    (tmp_path / "op").mkdir()
    dummy = tmp_path / "core" / "dummy_mod.py"
    dummy.write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    import core.dummy_mod  # noqa: F401
    yield tmp_path
    sys.modules.pop("core.dummy_mod", None)
    sys.modules.pop("core", None)


def _toca_modulo(path, novo_conteudo, delta_segundos=5):
    path.write_text(novo_conteudo, encoding="utf-8")
    st = os.stat(path)
    os.utime(path, (st.st_atime + delta_segundos, st.st_mtime + delta_segundos))


class TestReloadStaleModules:
    def test_recarrega_modulo_alterado(self, integrador_tmp):
        import core.dummy_mod
        assert core.dummy_mod.VALUE == 1

        _toca_modulo(integrador_tmp / "core" / "dummy_mod.py", "VALUE = 2\n")
        reloaded = reload_stale_modules(str(integrador_tmp))

        assert reloaded == ["core.dummy_mod"]
        assert core.dummy_mod.VALUE == 2

    def test_primeira_passada_recarrega_incondicionalmente(self, integrador_tmp):
        """Módulo importado ANTES do reloader existir (worker já em execução,
        estado do incidente #19062): sem carimbo, a primeira passada recarrega
        para sincronizar a memória com o disco."""
        import core.dummy_mod
        core.dummy_mod.VALUE = 99  # memória divergente do disco (VALUE = 1)

        reloaded = reload_stale_modules(str(integrador_tmp))

        assert reloaded == ["core.dummy_mod"]
        assert core.dummy_mod.VALUE == 1  # voltou a bater com o disco

    def test_nao_recarrega_sem_alteracao(self, integrador_tmp):
        reload_stale_modules(str(integrador_tmp))  # primeira passada: carimba mtime
        reloaded = reload_stale_modules(str(integrador_tmp))
        assert reloaded == []

    def test_ignora_modulos_fora_da_raiz(self, integrador_tmp):
        import json  # módulo stdlib fora da raiz do integrador
        json.__load_mtime__ = 0  # mesmo forçando mtime antigo
        try:
            reloaded = reload_stale_modules(str(integrador_tmp))
            assert "json" not in reloaded
        finally:
            del json.__load_mtime__

    def test_raiz_inexistente_nao_erro(self):
        assert reload_stale_modules("C:/caminho/que/nao/existe") == []
