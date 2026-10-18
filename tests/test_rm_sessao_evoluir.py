"""
QA — Sessão Evoluir compartilhada + retomada de extrações (incidente 2026-09-04).

O lote fazia 1 login do portal por extração (281 logins seguidos do IP do
Render) e mantinha transação aberta durante download+Gemini — colaborou para
derrubar o free tier. Cobrem aqui:
- cache de sessão com TTL (1 login para N downloads) + rollback da transação;
- re-login com retry único quando o portal recusa a sessão em cache;
- lote repassa a sessão entre extrações e a descarta em falha do portal;
- resume_stuck_extractions: só registros antigos, 1 thread sequencial.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

import routes.jobs as routes_jobs
from services import relatorio_rm_service as svc


@pytest.fixture(autouse=True)
def _cache_limpo():
    svc._invalidate_evoluir_session_cache()
    yield
    svc._invalidate_evoluir_session_cache()


def _sessao_evoluir(monkeypatch, respostas=(200,)):
    """get_evoluir_session mockado contando logins; session.get responde na ordem."""
    logins = []
    gets = []

    def _get(url, timeout=None):
        resp = MagicMock(status_code=respostas[len(gets)] if len(gets) < len(respostas) else 200)
        resp.headers = {"content-type": "application/pdf"}
        resp.content = b"%PDF-fake"
        resp.text = ""
        gets.append(url)
        return resp

    def _login(db, user_id):
        sessao = MagicMock()
        sessao.get.side_effect = _get
        logins.append(user_id)
        return sessao

    monkeypatch.setattr(routes_jobs, "get_evoluir_session", _login)
    return logins


class TestCacheSessaoEvoluir:

    def test_um_login_para_varios_downloads(self, monkeypatch):
        logins = _sessao_evoluir(monkeypatch)
        db = MagicMock()

        svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/a.pdf")
        svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/b.pdf")
        svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/c.pdf")

        assert len(logins) == 1

    def test_rollback_encerra_transacao_apos_login(self, monkeypatch):
        _sessao_evoluir(monkeypatch)
        db = MagicMock()
        svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/a.pdf")
        db.rollback.assert_called_once()

    def test_ttl_expirado_refaz_login(self, monkeypatch):
        logins = _sessao_evoluir(monkeypatch)
        db = MagicMock()
        svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/a.pdf")
        # envelhecer o cache além do TTL
        svc._evoluir_session_cache["ts"] -= (svc._EVOLUIR_SESSION_TTL + 1)
        svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/a.pdf")
        assert len(logins) == 2

    def test_sessao_recusada_refaz_login_e_repete_uma_vez(self, monkeypatch):
        logins = _sessao_evoluir(monkeypatch, respostas=(401, 200))
        db = MagicMock()

        content, mime = svc.download_rm_file(db, 14, "https://sistemaevoluir.com.br/resources/a.pdf")

        assert content == b"%PDF-fake" and mime == "application/pdf"
        assert len(logins) == 2  # login inicial + re-login após 401

    def test_sessao_passada_explícita_não_consulta_cache(self, monkeypatch):
        logins = _sessao_evoluir(monkeypatch)
        sessao = MagicMock()
        resp = MagicMock(status_code=200, content=b"img")
        resp.headers = {"content-type": "image/jpeg"}
        sessao.get.return_value = resp

        content, mime = svc.download_rm_file(MagicMock(), 14,
                                             "https://sistemaevoluir.com.br/resources/a.jpg",
                                             evoluir_session=sessao)

        assert (content, mime) == (b"img", "image/jpeg")
        assert logins == []


class TestLoteRepassaSessao:

    def test_extracoes_recebem_sessao_do_cache(self, monkeypatch):
        sessao_cacheada = MagicMock()
        svc._evoluir_session_cache.update(user_id=14, session=sessao_cacheada, ts=svc.time.time())

        processadas = []
        monkeypatch.setattr(svc, "_process_extraction_bg",
                            lambda eid, evoluir_session=None: processadas.append((eid, evoluir_session)))

        svc._process_extraction_lote_bg([1, 2, 3])

        assert processadas[0] == (1, None)          # primeira ainda sem sessão
        assert processadas[1] == (2, sessao_cacheada)  # seguintes reaproveitam
        assert processadas[2] == (3, sessao_cacheada)

    def test_falha_do_portal_descarta_sessao(self, monkeypatch):
        sessao_cacheada = MagicMock()
        svc._evoluir_session_cache.update(user_id=14, session=sessao_cacheada, ts=svc.time.time())

        def _fake(eid, evoluir_session=None):
            if evoluir_session is not None:
                raise ValueError("Falha ao baixar arquivo do portal Evoluir (401): x")
            return None

        processadas = []
        monkeypatch.setattr(svc, "_process_extraction_bg",
                            lambda eid, evoluir_session=None: (processadas.append(eid), _fake(eid, evoluir_session)))

        svc._process_extraction_lote_bg([1, 2])

        assert processadas == [1, 2]  # falha não derruba o lote
        assert svc._evoluir_session_cache["session"] is None  # cache invalidado


class TestResumeStuck:

    def _db(self, registros):
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value.all.return_value = registros
        db.query.return_value = q
        return db

    def test_retoma_apenas_antigos_em_thread_unica(self, monkeypatch):
        antigo = datetime.now(timezone.utc) - timedelta(minutes=30)
        registros = [SimpleNamespace(id=i, updated_at=antigo) for i in (1, 2, 3)]
        db = self._db(registros)

        thread_mock = MagicMock()
        monkeypatch.setattr(svc.threading, "Thread", thread_mock)

        n = svc.resume_stuck_extractions(db)

        assert n == 3
        # reset de EM_PROCESSAMENTO -> NAO_PROCESSADO
        update_args = db.query.return_value.filter.return_value.update.call_args
        assert update_args.args[0] == {"status_extracao": "NAO_PROCESSADO"}
        # UMA thread sequencial processando o lote
        thread_mock.assert_called_once()
        assert thread_mock.call_args.kwargs["target"] == svc._process_extraction_lote_bg
        assert thread_mock.call_args.kwargs["args"] == ([1, 2, 3],)
        thread_mock.return_value.start.assert_called_once()

    def test_sem_registros_nao_cria_thread(self, monkeypatch):
        db = self._db([])
        thread_mock = MagicMock()
        monkeypatch.setattr(svc.threading, "Thread", thread_mock)
        assert svc.resume_stuck_extractions(db) == 0
        thread_mock.assert_not_called()
