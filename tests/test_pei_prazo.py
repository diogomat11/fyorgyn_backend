"""
QA-01 — Testes do prazo PEI configurável (BE-02 / migration 0096).
"""
from datetime import date

from services.pei_service import _calcular_validade_pei


class TestCalcularValidadePei:

    def test_prazo_configuravel_90_dias(self):
        autorizacao = date(2026, 1, 1)
        assert _calcular_validade_pei(autorizacao, 90) == date(2026, 4, 1)

    def test_prazo_configuravel_365_dias(self):
        autorizacao = date(2026, 1, 1)
        assert _calcular_validade_pei(autorizacao, 365) == date(2027, 1, 1)

    def test_default_historico_180_dias(self):
        autorizacao = date(2026, 1, 1)
        # Sem prazo (None): fallback 180
        assert _calcular_validade_pei(autorizacao, None) == date(2026, 6, 30)

    def test_prazo_fora_do_range_faz_fallback_180(self):
        autorizacao = date(2026, 1, 1)
        assert _calcular_validade_pei(autorizacao, 10) == date(2026, 6, 30)   # abaixo de 30
        assert _calcular_validade_pei(autorizacao, 400) == date(2026, 6, 30)  # acima de 365

    def test_prazo_minimo_30(self):
        autorizacao = date(2026, 1, 1)
        assert _calcular_validade_pei(autorizacao, 30) == date(2026, 1, 31)
