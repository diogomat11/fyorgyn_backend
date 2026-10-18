"""
QA — Reconciliação automática de solicitações pendentes (incidente guia 25939699).

Cobrem:
- tenant com solicitação pendente stale + credenciais → enfileira exatamente 1
  job op11 com janela cobrindo a solicitação mais antiga;
- guard atômico do INSERT: job já em fila/execução não duplica;
- throttle por intervalo mínimo entre re-checagens do tenant;
- pendente recente (updated_at fresco) não dispara job;
- sem credenciais no convênio → não cria job;
- status terminal (autorizado/negado/cancelado) não é re-checado;
- convênio sem rotina de re-checagem mapeada é ignorado;
- janela de datas limitada ao teto configurável.
"""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import services.solicitacao_reconcile_service as svc


AGORA = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


def _sol(status="Aguardando documentação", updated=None, data_sol=None,
         user_id=14, convenio=6):
    return SimpleNamespace(
        user_id=user_id, id_convenio=convenio, base_guia_id=None,
        status_solicitacao=status,
        updated_at=updated or (AGORA - timedelta(days=3)),
        data_solicitacao=data_sol or (AGORA - timedelta(days=10)).date(),
    )


def _uconv():
    return SimpleNamespace(
        user_id=14, id_convenio=6, login="14895501",
        senha_criptografada="gAAA", cod_prestador="00631-2",
    )


class _ExecResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


def _db(grupos, pendentes, uconv=None, insert_cria=True, ultima_recheck=None):
    db = MagicMock()

    def _query(*models):
        model = models[0]
        # db.query(Solicitacao) vem a classe; db.query(Solicitacao.user_id, ...)
        # vem InstrumentedAttribute (resolver via mapper .parent.class_).
        name = getattr(model, "__name__", None) or getattr(
            getattr(getattr(model, "parent", None), "class_", None), "__name__", ""
        )
        q = MagicMock()
        if name == "Solicitacao":
            q.filter.return_value.distinct.return_value.all.return_value = grupos
            q.filter.return_value.all.return_value = pendentes
        elif name == "UserConvenio":
            q.filter.return_value.first.return_value = uconv
        return q

    db.query.side_effect = _query

    def _execute(stmt, params=None):
        sql = str(stmt)
        if "SELECT max(created_at)" in sql:
            return _ExecResult((ultima_recheck,))
        if "INSERT INTO worker.jobs" in sql:
            db.insert_params = params
            return _ExecResult({"id": 4242} if insert_cria else None)
        return _ExecResult(None)

    db.execute.side_effect = _execute
    return db


class TestCriaJobRecheck:

    def setup_method(self):
        self._old_env = {k: os.environ.get(k) for k in
                         ("SOLICITACAO_RECHECK_INTERVAL_HOURS",
                          "SOLICITACAO_RECHECK_MAX_WINDOW_DAYS")}
        os.environ.pop("SOLICITACAO_RECHECK_INTERVAL_HOURS", None)
        os.environ.pop("SOLICITACAO_RECHECK_MAX_WINDOW_DAYS", None)

    def teardown_method(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_stale_com_credenciais_cria_um_job(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        db = _db(grupos=[(14, 6)], pendentes=[_sol()], uconv=_uconv())

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 1
        db.commit.assert_called_once()
        params = db.insert_params
        assert params["rotina"] == "op11_import_guias_api"
        assert params["user_id"] == 14
        assert params["id_convenio"] == 6
        import json
        p = json.loads(params["params"])
        assert p["origem_reconcile"] is True
        assert p["login"] == "14895501"
        assert p["codigoPrestador"] == "00631-2"
        # janela cobre a solicitação mais antiga (data_solicitacao 22/09 - 1 dia)
        assert p["data_ini"] == "21/09/2026"
        assert p["data_fim"] == "02/10/2026"

    def test_guard_atomico_job_em_andamento_nao_duplica(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        db = _db(grupos=[(14, 6)], pendentes=[_sol()], uconv=_uconv(),
                 insert_cria=False)

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 0
        assert resumo["job_em_andamento"] == 1

    def test_intervalo_curto_entre_recheckings(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        db = _db(grupos=[(14, 6)], pendentes=[_sol()], uconv=_uconv(),
                 ultima_recheck=AGORA - timedelta(hours=1))

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 0
        assert resumo["intervalo_curto"] == 1

    def test_pendente_recente_nao_dispara(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        db = _db(grupos=[(14, 6)],
                 pendentes=[_sol(updated=AGORA - timedelta(minutes=30))],
                 uconv=_uconv())

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 0
        assert resumo["sem_stale"] == 1

    def test_sem_credenciais_nao_cria(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        db = _db(grupos=[(14, 6)], pendentes=[_sol()], uconv=None)

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 0
        assert resumo["sem_credenciais"] == 1

    def test_status_terminal_nao_e_rechecado(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        pendentes = [
            _sol(status="Autorizado"),
            _sol(status="Negado"),
            _sol(status="Cancelado (Aguardando documentação)"),
        ]
        db = _db(grupos=[(14, 6)], pendentes=pendentes, uconv=_uconv())

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 0

    def test_convenio_sem_rotina_mapeada_e_ignorado(self, monkeypatch):
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        db = _db(grupos=[(14, 3)], pendentes=[_sol(convenio=3)], uconv=_uconv())

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["grupos_avaliados"] == 0
        assert resumo["jobs_criados"] == 0

    def test_janela_limitada_ao_teto(self, monkeypatch):
        os.environ["SOLICITACAO_RECHECK_MAX_WINDOW_DAYS"] = "30"
        monkeypatch.setattr(svc, "datetime", _FakeDateTime)
        # solicitação de 1 ano atrás: janela não pode começar antes de hoje-30d
        db = _db(grupos=[(14, 6)],
                 pendentes=[_sol(data_sol=(AGORA - timedelta(days=365)).date())],
                 uconv=_uconv())

        resumo = svc.run_reconcile_pending_solicitacoes(db)

        assert resumo["jobs_criados"] == 1
        import json
        p = json.loads(db.insert_params["params"])
        assert p["data_ini"] == "02/09/2026"  # 30 dias antes de 02/10/2026

    def test_erro_interno_nao_propaga(self, monkeypatch):
        db = MagicMock()
        db.query.side_effect = RuntimeError("boom")
        resumo = svc.run_reconcile_pending_solicitacoes(db)
        assert resumo["jobs_criados"] == 0


class _FakeDateTime:
    """datetime fixo para testes determinísticos da janela de datas."""
    @staticmethod
    def now(tz=None):
        return AGORA


class TestStatusRecheck:

    def test_pendente_e_variacoes(self):
        for s in ("Pendente", "Aguardando documentação", "Em análise (Respondida)", None, ""):
            assert svc._is_status_recheck(s) is True

    def test_terminais(self):
        for s in ("Autorizado", "Autorizada", "Parcialmente autorizada",
                  "Liberada", "Negado", "Cancelado (Aguardando documentação)"):
            assert svc._is_status_recheck(s) is False
