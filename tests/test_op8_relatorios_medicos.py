"""
QA — OP8 Evoluir: importação de relatórios médicos (painel/relatorios/relatorio-medico).

Cobrem:
- parser da op8 com o HTML real capturado do portal (Heitor Antônio... Ipasgo);
- modo paginado: para quando a página repete conteúdo (portal ignora page/per_page);
- consumo _consume_evoluir/op8: insert novo, update de existente, resolução de
  id_paciente por nome normalizado (modo "todos") e skip de não resolvido;
- download autenticado do portal para extração Gemini (download_rm_file).
"""
import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import models as m
from services.guias_sync_service import _consume_evoluir

_OP8_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "Local_worker", "Worker", "100-evoluir", "op", "op8_importRelatoriosMedicos.py",
))

spec = importlib.util.spec_from_file_location("op8_importRelatoriosMedicos", _OP8_PATH)
op8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op8)


HTML_PORTAL = """
<table class="table">
  <thead><tr><th>Paciente</th><th>Plano</th><th>Médico</th><th>Data Doc</th><th>Validade</th><th>Arquivo</th></tr></thead>
  <tbody>
    <tr>
      <td class="text-start">Heitor Antônio Rodrigues De Souza</td>
      <td class="text-start">Ipasgo</td>
      <td class="text-start">Sem Médico</td>
      <td>26/05/2021</td>
      <td class="linha-atrasada">26/11/2021</td>
      <td><a href="https://sistemaevoluir.com.br/resources/storage/arquivosusuario/4802b81b_ea2a_4003_a626_7f3691e88265.pdf" target="_blank">[File]</a></td>
    </tr>
    <tr>
      <td class="text-start">Heitor Antônio Rodrigues De Souza</td>
      <td class="text-start">Ipasgo</td>
      <td class="text-start">Dra. Helena</td>
      <td>14/02/2026</td>
      <td>14/08/2026</td>
      <td><a href="https://sistemaevoluir.com.br/resources/storage/arquivosusuario/9999aaaa_bbbb.pdf" target="_blank">[File]</a></td>
    </tr>
    <tr><td colspan="6">Nenhum registro</td></tr>
  </tbody>
</table>
"""


class TestParserOp8:

    def test_html_real_do_portal(self):
        rows = op8.parse_relatorios_html(HTML_PORTAL)
        assert len(rows) == 2

        r1 = rows[0]
        assert r1["nome_paciente"] == "Heitor Antônio Rodrigues De Souza"
        assert r1["plano"] == "Ipasgo"
        assert r1["nome_medico"] == "Sem Médico"
        assert r1["data"] == "2021-05-26"
        assert r1["data_validade"] == "2021-11-26"
        assert r1["id_relatorio"] == "4802b81b_ea2a_4003_a626_7f3691e88265"
        assert r1["url_arquivo"].endswith("4802b81b_ea2a_4003_a626_7f3691e88265.pdf")

        r2 = rows[1]
        assert r2["nome_medico"] == "Dra. Helena"
        assert r2["data"] == "2026-02-14"
        assert r2["data_validade"] == "2026-08-14"
        assert r2["id_relatorio"] == "9999aaaa_bbbb"

    def test_linhas_sem_link_sao_ignoradas(self):
        assert op8.parse_relatorios_html("<tr><td>a</td><td>b</td><td>c</td><td>d</td><td>e</td><td>f</td></tr>") == []


class TestRunOp8Paginacao:

    def _scraper(self, paginas):
        scraper = MagicMock()
        sessao = MagicMock()

        def _get(url, timeout=None):
            page = 1
            if "page=2" in url:
                page = 2
            resp = MagicMock()
            resp.status_code = 200
            resp.text = paginas.get(page, "")
            return resp

        sessao.get.side_effect = _get
        scraper.session = sessao
        scraper.log = MagicMock()
        return scraper

    def test_todos_para_quando_pagina_repete(self):
        scraper = self._scraper({1: HTML_PORTAL, 2: HTML_PORTAL})
        result = op8.run(scraper, {"job_id": 1})  # sem paciente_id -> modo todos
        assert len(result) == 2
        assert scraper.session.get.call_count == 2  # página 2 repetida -> parou

    def test_por_paciente_adiciona_id(self):
        scraper = self._scraper({1: HTML_PORTAL})
        result = op8.run(scraper, {"job_id": 2, "paciente_id": "f2560cff-5915-47aa-9e70-ec21cc9d3ac6"})
        assert all(r["id_paciente"] == "f2560cff-5915-47aa-9e70-ec21cc9d3ac6" for r in result)
        assert "paciente_id=f2560cff" in scraper.session.get.call_args[0][0]
        # modo por paciente não pagina
        assert scraper.session.get.call_count == 1


def _db_op8(carteirinhas=(), rm_existente=None):
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is m.Carteirinha:
            q.filter.return_value.all.return_value = list(carteirinhas)
        elif model is m.RelatorioMedico:
            q.filter.return_value.first.return_value = rm_existente
        return q

    db.query.side_effect = _query
    return db


def _job_op8(result_rows, params="{}"):
    return SimpleNamespace(
        id=300, rotina="op8_importRelatoriosMedicos", id_convenio=100, user_id=14,
        carteirinha_id=None, params=params,
        result_data={"status": "success", "data": result_rows},
        result_consumed=False,
    )


RM_PORTAL = {
    "nome_paciente": "Heitor Antônio Rodrigues De Souza",
    "plano": "Ipasgo",
    "nome_medico": "Sem Médico",
    "data": "2021-05-26",
    "data_validade": "2021-11-26",
    "url_arquivo": "https://sistemaevoluir.com.br/resources/storage/arquivosusuario/4802b81b_ea2a_4003_a626_7f3691e88265.pdf",
    "id_relatorio": "4802b81b_ea2a_4003_a626_7f3691e88265",
}


def _counts():
    return {"jobs_processed": 0, "inserted": 0, "updated": 0, "skipped": 0}


class TestConsumoOp8:

    def test_insert_novo_com_id_paciente_do_param(self):
        db = _db_op8()
        job = _job_op8([dict(RM_PORTAL, id_paciente="f2560cff-0000")])
        counts = _counts()

        _consume_evoluir(db, job, counts)

        rm = db.add.call_args[0][0]
        assert rm.id_paciente == "f2560cff-0000"
        assert rm.id_relatorio == "4802b81b_ea2a_4003_a626_7f3691e88265"
        assert rm.data_relatorio is not None and rm.data_validade is not None
        assert rm.nome_medico == "Sem Médico"
        assert rm.observacoes == "Plano: Ipasgo"
        assert counts["inserted"] == 1

    def test_modo_todos_resolve_id_paciente_por_nome(self):
        # carteirinha com nome acentuado/case diferente do portal
        cart = SimpleNamespace(paciente="heitor antônio rodrigues de souza",
                               id_paciente="f2560cff-5915")
        db = _db_op8(carteirinhas=[cart])
        job = _job_op8([RM_PORTAL])  # sem id_paciente na linha
        counts = _counts()

        _consume_evoluir(db, job, counts)

        rm = db.add.call_args[0][0]
        assert rm.id_paciente == "f2560cff-5915"
        assert counts["inserted"] == 1

    def test_paciente_nao_resolvido_e_pulado(self):
        db = _db_op8(carteirinhas=[])  # nenhuma carteirinha
        job = _job_op8([RM_PORTAL])
        counts = _counts()

        _consume_evoluir(db, job, counts)

        db.add.assert_not_called()
        assert counts.get("rm_sem_paciente") == 1

    def test_rm_existente_e_atualizada(self):
        existente = SimpleNamespace(
            nome_paciente="antigo", url_arquivo=None, nome_medico=None,
            data_relatorio=None, data_validade=None, observacoes=None,
        )
        db = _db_op8(rm_existente=existente)
        job = _job_op8([dict(RM_PORTAL, id_paciente="f2560cff-0000")])
        counts = _counts()

        _consume_evoluir(db, job, counts)

        db.add.assert_not_called()
        assert existente.nome_paciente == "Heitor Antônio Rodrigues De Souza"
        assert existente.data_validade is not None
        assert counts["updated"] == 1


class TestDownloadAutenticadoPortal:

    def test_url_do_portal_usa_sessao_evoluir(self, monkeypatch):
        import routes.jobs as routes_jobs
        from services.relatorio_rm_service import download_rm_file

        resp = MagicMock(status_code=200, content=b"%PDF-fake")
        resp.headers = {"content-type": "application/pdf"}
        sessao = MagicMock()
        sessao.get.return_value = resp
        monkeypatch.setattr(routes_jobs, "get_evoluir_session", lambda db, uid: sessao)

        content, mime = download_rm_file(MagicMock(), 14,
                                         "https://sistemaevoluir.com.br/resources/storage/arquivosusuario/x.pdf")
        assert content == b"%PDF-fake"
        assert mime == "application/pdf"

    def test_url_publica_segue_download_direto(self, monkeypatch):
        from services import relatorio_rm_service as svc

        monkeypatch.setattr(svc, "download_file_from_url", lambda url: (b"ok", "application/pdf"))
        content, _ = svc.download_rm_file(MagicMock(), 14, "https://exemplo.com/a.pdf")
        assert content == b"ok"
