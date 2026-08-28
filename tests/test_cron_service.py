"""
QA-01 — Testes do cron service (BE-05).

Cobrem: serialização do painel, validação de horário, agrupamento de
carteirinhas por (user, convenio), pulo de grupos sem credencial e a janela
de execução (due / não-due / catch-up / idempotência por ultimo_run).
"""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from services import cron_service
from services.cron_service import (
    _ativos_by_grupo,
    list_crons,
    run_due_crons,
    update_cron_config,
)


def _cron(enabled=True, horario=time(23, 1), ultimo_run=None, id_integrador=3, rotina="op1_consulta"):
    return SimpleNamespace(
        id=1,
        id_integrador=id_integrador,
        enabled=enabled,
        horario=horario,
        fuso="America/Sao_Paulo",
        rotina=rotina,
        ultimo_run=ultimo_run,
    )


def _db_query_return(value):
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = value
    return db


class TestListCrons:

    def test_serializacao_painel(self):
        cron = _cron(ultimo_run=date(2026, 8, 15))
        db = MagicMock()
        # 1a query: WorkerIntegrador (.all direto); 2a: CronConfig (.order_by().all)
        db.query.return_value.all.return_value = [SimpleNamespace(id_integrador=3, nome="Unimed Goiânia")]
        db.query.return_value.order_by.return_value.all.return_value = [cron]
        result = list_crons(db)
        assert len(result) == 1
        assert result[0]["nome_integrador"] == "Unimed Goiânia"
        assert result[0]["horario"] == "23:01"
        assert result[0]["ultimo_run"] == "2026-08-15"
        assert result[0]["enabled"] is True


class TestUpdateCronConfig:

    def test_horario_invalido_rejeitado(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _cron()
        with pytest.raises(Exception):
            update_cron_config(db, 1, horario="25:99")

    def test_horario_valido_aplicado(self):
        cron = _cron()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = cron
        update_cron_config(db, 1, horario="20:01")
        assert cron.horario == time(20, 1)


class TestAtivosByGrupo:

    def test_agrupa_por_user_e_convenio(self):
        rows = [
            (101, 1, 3),
            (102, 1, 3),
            (201, 2, 3),
            (301, None, 3),
        ]
        db = MagicMock()
        # 1a query: convenios do integrador; 2a: carteirinhas
        db.query.return_value.filter.return_value.all.side_effect = [[(3,)], rows]
        grupos = _ativos_by_grupo(db, 3)
        assert set(grupos[(1, 3)]) == {101, 102}
        assert grupos[(2, 3)] == [201]
        assert grupos[(None, 3)] == [301]

    def test_integrador_sem_convenios(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        assert _ativos_by_grupo(db, 99) == {}


class TestRunCronJobs:

    def test_pula_grupo_sem_credencial(self):
        cron = _cron()
        rows = [(101, 1, 3)]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [
            [(3,)],   # convenios
            rows,     # carteirinhas
        ]
        # Lookup de UserConvenio -> None (sem credencial)
        db.query.return_value.filter.return_value.first.return_value = None
        with patch.object(cron_service, "_cron_logger"):
            resumo = cron_service.run_cron_jobs(db, cron)
        assert resumo["grupos_pulados_sem_credencial"] == 1
        assert resumo["jobs_criados"] == 0

    def test_cria_jobs_para_grupo_com_credencial(self):
        cron = _cron()
        rows = [(101, 1, 3)]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [
            [(3,)],       # convenios
            rows,         # carteirinhas
            SimpleNamespace(id=7),  # UserConvenio existe
        ]
        with patch.object(cron_service, "_cron_logger"), \
             patch("services.job_service.create_jobs_bulk", return_value=1) as mock_bulk:
            resumo = cron_service.run_cron_jobs(db, cron)
        assert resumo["jobs_criados"] == 1
        mock_bulk.assert_called_once_with(
            db, [101], id_convenio=3, rotina="op1_consulta", user_id=1,
            params='{"origem_cron": true, "cron_id": 1}')



class TestRunDueCrons:
    """Relógio injetado via param `now` (tz-aware) — sem depender do horário real."""

    TZ = ZoneInfo("America/Sao_Paulo")

    def _db(self, cron):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [cron]
        return db

    def test_nao_rodou_ainda_horario_futuro(self):
        cron = _cron(horario=time(23, 1))
        db = self._db(cron)
        res = run_due_crons(db, now=datetime(2026, 8, 15, 12, 0, tzinfo=self.TZ))
        assert res == []
        assert cron.ultimo_run is None

    def test_roda_no_horario_e_marca_ultimo_run(self):
        cron = _cron(horario=time(23, 1))
        db = self._db(cron)
        with patch.object(cron_service, "run_cron_jobs", return_value={"jobs_criados": 5}) as mock_run:
            res = run_due_crons(db, now=datetime(2026, 8, 15, 23, 5, tzinfo=self.TZ))
        assert cron.ultimo_run == date(2026, 8, 15)
        assert res[0]["jobs_criados"] == 5
        mock_run.assert_called_once()

    def test_idempotente_ja_rodou_hoje(self):
        cron = _cron(horario=time(23, 1), ultimo_run=date(2026, 8, 15))
        db = self._db(cron)
        res = run_due_crons(db, now=datetime(2026, 8, 15, 23, 30, tzinfo=self.TZ))
        assert res == []

    def test_catchup_rodou_ontem_e_horario_passou_hoje(self):
        cron = _cron(horario=time(23, 1), ultimo_run=date(2026, 8, 14))
        db = self._db(cron)
        with patch.object(cron_service, "run_cron_jobs", return_value={"jobs_criados": 2}):
            res = run_due_crons(db, now=datetime(2026, 8, 15, 23, 30, tzinfo=self.TZ))
        assert len(res) == 1
        assert cron.ultimo_run == date(2026, 8, 15)

    def test_cron_desabilitado_ignorado(self):
        # O filtro de habilitação acontece no SQL (CronConfig.enabled.is_(True));
        # o mock simula o retorno do banco: nenhum cron habilitado -> nada a fazer.
        cron = _cron(enabled=False)
        db = self._db(cron)
        db.query.return_value.filter.return_value.all.return_value = []
        res = run_due_crons(db, now=datetime(2026, 8, 15, 23, 30, tzinfo=self.TZ))
        assert res == []
        assert cron.ultimo_run is None

    def test_now_utc_convertido_para_fuso_do_cron(self):
        # 23:30 UTC = 20:30 em Brasília -> cron das 20:01 deve rodar
        cron = _cron(horario=time(20, 1))
        db = self._db(cron)
        with patch.object(cron_service, "run_cron_jobs", return_value={"jobs_criados": 1}):
            res = run_due_crons(db, now=datetime(2026, 8, 15, 23, 30, tzinfo=ZoneInfo("UTC")))
        assert len(res) == 1
        assert cron.ultimo_run == date(2026, 8, 15)
