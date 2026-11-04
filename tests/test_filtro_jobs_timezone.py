"""
QA — Filtro de data da listagem de jobs no fuso correto.

Caso real (PO 2026-10-22): o lote do Fature criado às 02:47 UTC de 22/10
(= 21/10 23:47 em São Paulo) aparecia na tela como 21/10 (created_at vem
localizado) mas NÃO era pego pelo filtro do dia 21/10 — a data crua era
comparada com created_at em UTC (off-by-3h).
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timezone_utils import (  # noqa: E402
    data_local_para_utc_inicio, data_local_para_utc_fim_exclusivo, TZ_SP,
)


def test_inicio_do_dia_local_vira_03h_utc():
    # 00:00 de São Paulo (UTC-3) = 03:00 UTC do mesmo dia
    assert data_local_para_utc_inicio(date(2026, 10, 21)) == datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)


def test_fim_exclusivo_cobre_o_dia_inteiro_local():
    # fim exclusivo de 21/10 local = 22/10 03:00 UTC
    assert data_local_para_utc_fim_exclusivo(date(2026, 10, 21)) == datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)


def test_lote_criado_23h47_local_cai_no_filtro_do_dia_21():
    """Caso do incidente: job criado 2026-10-22 02:47:35Z (= 21/10 23:47 local)
    deve ser pego pelo filtro 21/10 e NÃO pelo filtro 22/10."""
    criado = datetime(2026, 10, 22, 2, 47, 35, tzinfo=timezone.utc)
    assert data_local_para_utc_inicio(date(2026, 10, 21)) <= criado < data_local_para_utc_fim_exclusivo(date(2026, 10, 21))
    assert not (data_local_para_utc_inicio(date(2026, 10, 22)) <= criado < data_local_para_utc_fim_exclusivo(date(2026, 10, 22)))
    # sanidade: 23:47 local de fato
    from timezone_utils import localize_datetime
    assert localize_datetime(criado).strftime("%d/%m %H:%M") == "21/10 23:47"
