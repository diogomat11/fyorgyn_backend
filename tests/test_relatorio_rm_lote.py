"""
QA — Lote de extração Gemini (POST /api/relatorios/extrair-lote).

Cobrem queue_extraction_lote:
- agenda todos os RMs pendentes (com arquivo e id_relatorio sem extração);
- pula os já extraídos (dedup por id_relatorio) e os sem arquivo;
- RMs sem id_relatorio só entram com reprocessar=true (senão duplicariam);
- uma ÚNICA background task processa o lote em sequência.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import models as m
from services.relatorio_rm_service import queue_extraction_lote


def _rm(id, id_relatorio, url="https://sistemaevoluir.com.br/resources/storage/arquivosusuario/x.pdf"):
    return SimpleNamespace(
        id=id, user_id=14, is_active=True,
        id_paciente=f"pac-{id}", nome_paciente=f"Paciente {id}",
        id_relatorio=id_relatorio, url_arquivo=url,
    )


def _db(rms, ja_extraidos=()):
    db = MagicMock()

    def _query(entity):
        q = MagicMock()
        if entity is m.RelatorioMedico:
            q.filter.return_value.all.return_value = list(rms)
        elif getattr(entity, "table", None) is not None and entity.table.name == "relatorios_medicos_extracao":
            # query por coluna (RelatorioMedicoExtracao.id_relatorio)
            q.filter.return_value.all.return_value = [(r,) for r in ja_extraidos]
        return q

    db.query.side_effect = _query
    return db


class TestQueueExtractionLote:

    def test_agrega_pendentes_e_pula_deduplicados(self):
        # A pendente; B já extraído; D sem id_relatorio (sem chave de dedup);
        # E com URL vazia (banco retorna, código deve pular).
        rms = [_rm(1, "a"), _rm(2, "b"), _rm(4, None), _rm(5, "e", url="  ")]
        db = _db(rms, ja_extraidos=["b"])
        bt = MagicMock()

        res = queue_extraction_lote(db, user_id=14, background_tasks=bt)

        assert res == {"agendados": 1, "pulados_ja_extraidos": 2, "pulados_sem_arquivo": 1}
        # apenas o RM 'a' virou registro de extração
        criados = [c.args[0] for c in db.add.call_args_list]
        assert len(criados) == 1 and criados[0].id_relatorio == "a"
        assert criados[0].status_extracao == "NAO_PROCESSADO"
        # uma única task processa o lote inteiro
        bt.add_task.assert_called_once()
        task_fn, task_arg = bt.add_task.call_args[0]
        assert task_fn.__name__ == "_process_extraction_lote_bg"
        assert len(task_arg) == 1

    def test_reprocessar_inclui_ja_extraidos(self):
        rms = [_rm(1, "a"), _rm(2, "b"), _rm(4, None)]
        db = _db(rms, ja_extraidos=["b"])
        bt = MagicMock()

        res = queue_extraction_lote(db, user_id=14, background_tasks=bt, reprocessar=True)

        assert res["agendados"] == 3
        assert res["pulados_ja_extraidos"] == 0
        assert bt.add_task.call_args[0][1].__len__() == 3 or len(bt.add_task.call_args[0][1]) == 3

    def test_nada_a_agendar_nao_cria_task(self):
        db = _db([], ja_extraidos=[])
        bt = MagicMock()

        res = queue_extraction_lote(db, user_id=14, background_tasks=bt)

        assert res["agendados"] == 0
        db.add.assert_not_called()
        bt.add_task.assert_not_called()
