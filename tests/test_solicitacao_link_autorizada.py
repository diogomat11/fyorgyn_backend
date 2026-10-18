"""
QA — Vínculo solicitação↔guia autorizada no sync (incidente guia 25939699).

Cobrem:
- fallback tolerante: guia autorizada cujo codigo_terapia diverge do salvo na
  solicitação atualiza e vincula a única candidata pendente (antes ficava órfã
  para sempre: 'Aguardando documentação' na aba Solicitações com a guia
  autorizada na aba Guias);
- ambiguidade (duas candidatas pendentes para a mesma guia) não vincula nada;
- lote com a mesma guia autorizada E pendente (procedimentos irmãos com status
  distintos no portal) não apaga a base_guia recém-atualizada;
- guia listada apenas como pendente (cancelada no portal) continua saindo de
  base_guias — comportamento original preservado.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.guias_sync_service import bulk_upsert_guias_from_json


def _sol_pendente(**kw):
    base = dict(
        id=2141, user_id=1, carteirinha_id=5687, id_convenio=6,
        guia="25939699", codigo_terapia="00040045",
        status_solicitacao="Aguardando documentação",
        base_guia_id=None, job_id=18301,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _linha_base(**kw):
    base = dict(
        id=21121, user_id=1, carteirinha_id=5687, id_convenio=6,
        guia="25939699", guia_prestador=None, codigo_terapia="00040045",
        nome_terapia=None, senha=None, status_guia="Antigo",
        data_solicitacao=None, data_autorizacao=None, validade=None,
        qtde_solicitada=None, sessoes_autorizadas=None, sessoes_realizadas=None,
        saldo=None, timestamp_captura=None, codigo_beneficiario=None,
        cod_prestador=None, valida_prestador=None, updated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


GUIA_AUTORIZADA = {
    "numero_guia": "25939699",
    "status": "Autorizado",
    "codigo_terapia": "00040045",
    "senha": "25939699113",
    "qtde_solicitada": 10,
    "qtde_autorizada": 10,
}


def _db(solicitacao_first=None, candidatas=None, base_guias_pre_check=(),
        raced=None):
    """Sessão mock com filas por model. Solicitacao.first() respeita a ordem
    dos lookups: [chave exata, conflito] — por isso aceita lista de retornos."""
    db = MagicMock()
    if solicitacao_first is None or isinstance(solicitacao_first, list):
        first_returns = list(solicitacao_first or [])
    else:
        first_returns = [solicitacao_first]

    def _sol_first():
        return first_returns.pop(0) if first_returns else None

    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "Carteirinha":
            q.filter.return_value.all.return_value = []
        elif name == "BaseGuia":
            q.filter.return_value.all.return_value = list(base_guias_pre_check)
            q.filter.return_value.first.return_value = raced
        elif name == "Solicitacao":
            q.filter.return_value.first.side_effect = _sol_first
            q.filter.return_value.all.return_value = (
                candidatas if candidatas is not None else []
            )
        return q

    db.query.side_effect = _query
    return db


class TestFallbackTolerante:

    def test_unica_candidata_e_vinculada_ao_autorizar(self):
        # Listagem de guias traz terapia '00040045', mas a solicitação foi
        # gravada com outro código pelo fluxo de solicitação → antes: órfã.
        sol = _sol_pendente(codigo_terapia="00011193")
        existente = _linha_base(status_guia="Antigo")
        db = _db(solicitacao_first=[None, None], candidatas=[sol],
                 base_guias_pre_check=[existente])

        res = bulk_upsert_guias_from_json(db, [GUIA_AUTORIZADA],
                                          id_convenio=6, user_id=1)

        assert res["updated"] == 1
        assert sol.status_solicitacao == "Autorizado"
        assert sol.base_guia_id == 21121
        assert sol.senha == "25939699113"
        assert sol.sessoes_autorizadas == 10
        db.delete.assert_not_called()

    def test_chave_exata_continua_prevalecendo(self):
        sol = _sol_pendente()
        existente = _linha_base()
        db = _db(solicitacao_first=[sol, None], candidatas=[sol],
                 base_guias_pre_check=[existente])

        bulk_upsert_guias_from_json(db, [GUIA_AUTORIZADA], id_convenio=6, user_id=1)

        assert sol.status_solicitacao == "Autorizado"
        assert sol.base_guia_id == 21121
        db.delete.assert_not_called()

    def test_duas_candidatas_nao_vincula_nada(self):
        s1 = _sol_pendente(id=2141, codigo_terapia="00011193")
        s2 = _sol_pendente(id=2142, codigo_terapia="00011185")
        db = _db(solicitacao_first=[None, None], candidatas=[s1, s2])

        bulk_upsert_guias_from_json(db, [GUIA_AUTORIZADA], id_convenio=6, user_id=1)

        assert s1.status_solicitacao == "Aguardando documentação"
        assert s2.status_solicitacao == "Aguardando documentação"
        assert s1.base_guia_id is None and s2.base_guia_id is None

    def test_candidata_com_procedimento_pendente_no_lote_nao_e_adotada(self):
        # Guia com dois procedimentos: B autorizado agora, A ainda pendente e
        # presente no mesmo lote — a solicitação de A não pode ser marcada como
        # autorizada pelo fallback.
        sol_a = _sol_pendente(id=2141, codigo_terapia="00011193")
        pend_a = dict(GUIA_AUTORIZADA)
        pend_a["status"] = "Aguardando documentação"
        pend_a["codigo_terapia"] = "00011193"
        db = _db(solicitacao_first=[None, None], candidatas=[sol_a])

        bulk_upsert_guias_from_json(db, [GUIA_AUTORIZADA, pend_a],
                                    id_convenio=6, user_id=1)

        assert sol_a.status_solicitacao == "Aguardando documentação"
        assert sol_a.base_guia_id is None

    def test_sem_candidatas_insere_guia_sem_vinculo(self):
        db = _db(solicitacao_first=[None, None], candidatas=[])

        res = bulk_upsert_guias_from_json(db, [GUIA_AUTORIZADA], id_convenio=6, user_id=1)

        assert res["inserted"] == 1


class TestNaoApagaGuiaAutorizadaNoMesmoLote:

    def test_linha_irma_pendente_nao_apaga_base_guia(self):
        # Portal lista a mesma guia com dois procedimentos: um Autorizado,
        # outro Aguardando documentação — o segundo não pode apagar o primeiro.
        pend = dict(GUIA_AUTORIZADA)
        pend["status"] = "Aguardando documentação"
        raced = _linha_base(status_guia="Autorizado")
        db = _db(solicitacao_first=[None, None], candidatas=[], raced=raced)

        bulk_upsert_guias_from_json(db, [GUIA_AUTORIZADA, pend],
                                    id_convenio=6, user_id=1)

        db.delete.assert_not_called()

    def test_status_pendente_isolado_ainda_remove_base_guia_errada(self):
        # Guia listada APENAS como pendente (ex.: cancelada no portal) deve
        # sair de base_guias e voltar para solicitacoes — comportamento original.
        pend = dict(GUIA_AUTORIZADA)
        pend["status"] = "Cancelado (Aguardando documentação)"
        raced = _linha_base(status_guia="Autorizado")
        db = _db(solicitacao_first=[None, None], candidatas=[], raced=raced)

        bulk_upsert_guias_from_json(db, [pend], id_convenio=6, user_id=1)

        db.delete.assert_called_once()
        deletado = db.delete.call_args[0][0]
        assert deletado is raced
