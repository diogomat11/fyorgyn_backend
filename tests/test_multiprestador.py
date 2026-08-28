"""
QA-01 — Testes do fluxo multiprestador no hub (BE-06a).

Cobrem _split_capturadas_por_outro_prestador:
- separa itens "Capturada por outro prestador" do resultado da captura (op2);
- cria job de consulta (consulta_guias) com depending_id do job origem;
- não aplica quando o convenio não é multiprestador;
- ignora rotinas que não são de captura.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.guias_sync_service import _split_capturadas_por_outro_prestador


CAPTURADA_ITEM = {
    "numero_guia": "12345",
    "status": "Capturada por outro prestador",
    "detalhe": "Guia não localizada/exibida no portal",
    "multiprestador": True,
}
OK_ITEM = {"numero_guia": "67890", "timestamp_captura": "15/08/2026 10:00", "status": "Capturado"}


def _job(rotina="op2_captura", convenio_multiprestador=True):
    job = SimpleNamespace(
        id=42,
        rotina=rotina,
        id_convenio=3,
        user_id=1,
        carteirinha_id=77,
        params='{"login":"l","senha_criptografada":"s","cod_prestador":"111","webhook_url":"http://x","guias":["12345"]}',
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id_convenio=3, guias_multiprestador=convenio_multiprestador
    )
    return job, db


class TestSplitCapturadasPorOutroPrestador:

    def test_separa_itens_e_cria_job_consulta(self):
        job, db = _job()
        filtrados, qtd = _split_capturadas_por_outro_prestador(db, job, [CAPTURADA_ITEM, OK_ITEM])

        assert qtd == 1
        assert filtrados == [OK_ITEM]

        # Job de consulta criado
        db.add.assert_called_once()
        novo_job = db.add.call_args[0][0]
        assert novo_job.rotina == "consulta_guias"
        assert novo_job.carteirinha_id == 77
        assert novo_job.id_convenio == 3
        assert novo_job.user_id == 1
        assert novo_job.depending_id == 42
        assert novo_job.status == "pending"

    def test_params_do_job_consulta_mantem_credenciais_e_removem_guia(self):
        job, db = _job()
        _, _ = _split_capturadas_por_outro_prestador(db, job, [CAPTURADA_ITEM])
        novo_job = db.add.call_args[0][0]
        assert novo_job.params["login"] == "l"
        assert novo_job.params["cod_prestador"] == "111"
        assert novo_job.params["webhook_url"] == "http://x"
        # chaves de captura da guia específica removidas
        assert "guias" not in novo_job.params

    def test_convenio_nao_multiprestador_mantem_fluxo_antigo(self):
        job, db = _job(convenio_multiprestador=False)
        filtrados, qtd = _split_capturadas_por_outro_prestador(db, job, [CAPTURADA_ITEM, OK_ITEM])
        assert qtd == 0
        assert len(filtrados) == 2
        db.add.assert_not_called()

    def test_rotina_nao_captura_ignorada(self):
        job, db = _job(rotina="op1_consulta")
        filtrados, qtd = _split_capturadas_por_outro_prestador(db, job, [CAPTURADA_ITEM])
        assert qtd == 0
        assert filtrados == [CAPTURADA_ITEM]
        db.add.assert_not_called()

    def test_sem_itens_capturados_nao_cria_job(self):
        job, db = _job()
        filtrados, qtd = _split_capturadas_por_outro_prestador(db, job, [OK_ITEM])
        assert qtd == 0
        db.add.assert_not_called()

    def test_status_case_insensitive(self):
        job, db = _job()
        item = {**CAPTURADA_ITEM, "status": "  CAPTURADA POR OUTRO PRESTADOR  "}
        _, qtd = _split_capturadas_por_outro_prestador(db, job, [item])
        assert qtd == 1
