"""
T6 — Locks de regra do plano lote_strategy (plano_lote_strategy_unimed.md).

R2.1: itens da OP4 com dataRealizacao ANTERIOR ao início do lote permanecem
      vinculados ao lote (o vínculo vem do filtro, não da data do item).
R2.2: formato IPASGO inalterado (apenas data_fim; OP13; status Processando).
R2.3: auto-período mensal_fixo 18→17 (inclusive virada de ano), derivação de
      data_inicio a partir da data_fim e resolução de strategies (3/21 período,
      6/legado número).

Testes com DB real usam transação com ROLLBACK (nada persiste) e são pulados
quando DATABASE_URL não está disponível.
"""
import os
from datetime import date
from types import SimpleNamespace

import pytest

DB_URL = os.environ.get("DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(not DB_URL, reason="DATABASE_URL não configurada")


# ── R2.3: calcular_auto_periodo (puro) ───────────────────────────────────────

def test_auto_periodo_janela_18_17():
    from services.lote_strategy.unimed_periodo import calcular_auto_periodo as cap
    # Dia 18 inicia nova janela
    assert cap(18, 17, date(2026, 10, 18)) == (date(2026, 10, 18), date(2026, 11, 17))
    # Véspera ainda pertence à janela anterior
    assert cap(18, 17, date(2026, 10, 17)) == (date(2026, 9, 18), date(2026, 10, 17))
    assert cap(18, 17, date(2026, 10, 5)) == (date(2026, 9, 18), date(2026, 10, 17))
    # Virada de ano
    assert cap(18, 17, date(2026, 12, 25)) == (date(2026, 12, 18), date(2027, 1, 17))
    # Janela dentro do mesmo mês (1→31)
    assert cap(1, 31, date(2026, 10, 10)) == (date(2026, 10, 1), date(2026, 10, 31))


def test_auto_periodo_config_invalida():
    from services.lote_strategy.unimed_periodo import calcular_auto_periodo as cap
    assert cap(None, 17, date(2026, 10, 10)) is None
    assert cap(18, None, date(2026, 10, 10)) is None


# ── R2.3: derivação/validação da strategy Unimed (sem DB) ────────────────────

def _strategy_com_convenio(tipo, dia_ini=None, dia_fim=None):
    from services.lote_strategy.unimed_periodo import UnimedPeriodoLoteStrategy
    s = UnimedPeriodoLoteStrategy()
    conv = SimpleNamespace(id_convenio=3, nome="UNIMED GOIANIA",
                           lote_periodo_tipo=tipo, lote_dia_inicio=dia_ini, lote_dia_fim=dia_fim)
    s._convenio = lambda db, cid: conv
    return s


def _user(id=1):
    return SimpleNamespace(id=id, is_admin=False)


def test_unimed_livre_exige_data_inicio():
    from fastapi import HTTPException
    s = _strategy_com_convenio("livre")
    req = SimpleNamespace(id_convenio=3, cod_prestador="2209525",
                          data_fim=date(2026, 10, 17), data_inicio=None)
    with pytest.raises(HTTPException) as e:
        s.criar_lote(None, _user(), req)
    assert e.value.status_code == 400


def test_unimed_criar_lote_nao_dispara_job(monkeypatch):
    """Decisão (2026-10-18): criar lote Unimed NÃO cria job OP4 automático —
    o job nasce pela sincronização do lote ou pelo módulo Importações."""
    s = _strategy_com_convenio("mensal_fixo", 18, 17)

    import routes.jobs as jobs_mod
    def _fail_op4(*a, **kw):
        raise AssertionError("criar_lote não deve disparar job OP4")
    monkeypatch.setattr(jobs_mod, "create_goiania_op4_job", _fail_op4)

    criado = {}
    class _Db:
        def add(self, o):
            o.id_lote = 9901
            criado["lote"] = o
        def flush(self):
            pass
        def commit(self):
            pass

    req = SimpleNamespace(id_convenio=3, cod_prestador="2209525",
                          data_fim=date(2026, 10, 17), data_inicio=None)
    ret = s.criar_lote(_Db(), _user(), req)

    assert criado["lote"].data_inicio == date(2026, 9, 18)
    assert criado["lote"].data_fim == date(2026, 10, 17)
    assert criado["lote"].status == "Aberto"
    assert "18/09/2026" in ret["message"]
    assert "sincronização" in ret["message"].lower()


def test_unimed_periodo_invertido_400(monkeypatch):
    from fastapi import HTTPException
    s = _strategy_com_convenio("livre")
    req = SimpleNamespace(id_convenio=3, cod_prestador="x",
                          data_fim=date(2026, 9, 17), data_inicio=date(2026, 10, 18))
    with pytest.raises(HTTPException) as e:
        s.criar_lote(None, _user(), req)
    assert e.value.status_code == 400


# ── DB real (rollback): resolução, form-config, upsert R2.1 e IPASGO ─────────

@pytest.fixture()
def db_rollback():
    from database import engine
    from sqlalchemy.orm import Session
    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        outer.rollback()
        db.close()
        conn.close()


@needs_db
@pytest.mark.parametrize("conv_id,classe", [
    (3, "UnimedPeriodoLoteStrategy"),
    (21, "UnimedPeriodoLoteStrategy"),
    (6, "IpasgoLoteStrategy"),
])
def test_resolucao_strategy(db_rollback, conv_id, classe):
    from services.lote_strategy import get_lote_strategy
    s = get_lote_strategy(db_rollback, conv_id)
    assert type(s).__name__ == classe


@needs_db
def test_form_config_conv3_auto_periodo(db_rollback):
    from services.lote_strategy import get_lote_strategy
    from models import Convenio
    conv = db_rollback.query(Convenio).filter(Convenio.id_convenio == 3).first()
    cfg = get_lote_strategy(db_rollback, 3).form_config(db_rollback, conv)
    assert cfg["tipo_formulario"] == "periodo"
    assert cfg["rotina_sync"] == "4"
    assert cfg["lote_periodo_tipo"] == "mensal_fixo"
    assert cfg["auto_periodo"] and "data_ini" in cfg["auto_periodo"]


@needs_db
def test_item_data_anterior_permanece_no_lote(db_rollback):
    """
    R2.1: lote 18/09→17/10; chunk OP4 traz sessão datada 15/09 → deve ser
    inserida NO lote com dataRealizacao=15/09. O vínculo vem do filtro da OP4,
    não da data do item — este teste proíbe filtro futuro por data.
    """
    from models import Job, FaturamentoLote
    from services.guias_sync_service import _upsert_op4_guias

    job = db_rollback.query(Job).filter(Job.id == 19152).first()
    assert job is not None, "fixture depende do job 19152 existir"

    counts = {"jobs_processed": 0, "inserted": 0, "updated": 0, "skipped": 0}
    chunk = [{
        "cd_guia": "99000101", "guia": "99QA01", "carteirinha": "111 - TESTE",
        "cod_procedimento": "2250005103",
        "series": [{"seq": 1, "data": "15/09/2026", "hora": "08:00"}],  # anterior a 18/09
    }]
    inserted, _ = _upsert_op4_guias(db_rollback, job, chunk, counts, id_lote=65)
    assert inserted >= 1

    item = db_rollback.query(FaturamentoLote).filter(
        FaturamentoLote.detalheId == 990001011).first()
    assert item is not None, "item com data anterior deve ser inserido"
    assert item.id_lote == 65, "item deve pertencer ao lote do período"
    assert item.dataRealizacao == date(2026, 9, 15), "data original da sessão preservada"


@needs_db
def test_validador_intervalo_lote_unico(db_rollback):
    """
    Regra (2026-10-18): job OP4 deve permear APENAS UM lote.
    Cenários com lotes 18/09/2027→17/10/2027 (A) e 18/10/2027→17/11/2027 (B) — 2027 para não colidir com lotes reais de 2026 do mesmo usuário:
    sub-intervalo de A ok; intervalo cruzando A e B → 400; extrapolando A → 400;
    sem lote → 400; lote cancelado é ignorado.
    """
    from fastapi import HTTPException
    from models import LoteConvenio
    from services.lote_strategy.unimed_periodo import validar_intervalo_em_lote_unico

    def _novo_lote(ini, fim, status="Aberto"):
        l = LoteConvenio(id_convenio=3, user_id=1, cod_prestador="TESTE",
                         data_inicio=ini, data_fim=fim, status=status)
        db_rollback.add(l)
        return l

    lote_a = _novo_lote(date(2027, 9, 18), date(2027, 10, 17))
    lote_b = _novo_lote(date(2027, 10, 18), date(2027, 11, 17))
    db_rollback.flush()

    # Sub-intervalo contido em A → ok, retorna A
    r = validar_intervalo_em_lote_unico(db_rollback, 3, 1, date(2027, 9, 20), date(2027, 9, 25))
    assert r.id_lote == lote_a.id_lote

    # Intervalo exato do lote → ok
    r = validar_intervalo_em_lote_unico(db_rollback, 3, 1, date(2027, 9, 18), date(2027, 10, 17))
    assert r.id_lote == lote_a.id_lote

    # Cruza A e B → inválido
    with pytest.raises(HTTPException) as e:
        validar_intervalo_em_lote_unico(db_rollback, 3, 1, date(2027, 10, 1), date(2027, 10, 20))
    assert e.value.status_code == 400 and "mais de um lote" in e.value.detail

    # Extrapola o início de A → inválido
    with pytest.raises(HTTPException) as e:
        validar_intervalo_em_lote_unico(db_rollback, 3, 1, date(2027, 9, 5), date(2027, 9, 25))
    assert e.value.status_code == 400 and "extrapola" in e.value.detail

    # Nenhum lote no intervalo → inválido
    with pytest.raises(HTTPException) as e:
        validar_intervalo_em_lote_unico(db_rollback, 3, 1, date(2027, 8, 1), date(2027, 8, 30))
    assert e.value.status_code == 400 and "Nenhum lote" in e.value.detail

    # Lote cancelado é ignorado
    lote_a.status = "Cancelado"
    db_rollback.flush()
    with pytest.raises(HTTPException) as e:
        validar_intervalo_em_lote_unico(db_rollback, 3, 1, date(2027, 9, 20), date(2027, 9, 25))
    assert e.value.status_code == 400 and "Nenhum lote" in e.value.detail


@needs_db
def test_ipasgo_strategy_exige_apenas_data_fim(db_rollback):
    from services.lote_strategy.ipasgo import IpasgoLoteStrategy
    from models import LoteConvenio, Job

    req = SimpleNamespace(id_convenio=6, cod_prestador="TESTE",
                          data_fim=date(2026, 10, 17), data_inicio=None)
    ret = IpasgoLoteStrategy().criar_lote(db_rollback, _user(), req)

    lote = db_rollback.query(LoteConvenio).filter(
        LoteConvenio.id_lote == ret["id_lote"]).first()
    assert lote.status == "Processando" and lote.numero_lote is None

    job = db_rollback.query(Job).filter(
        Job.id_convenio == 6, Job.rotina == "13").order_by(Job.id.desc()).first()
    assert job is not None, "OP13 enfileirada como hoje"


# ── Cancelamento de lote (2026-10-18, rev. 3) ────────────────────────────────

def _user_admin(id=1):
    return SimpleNamespace(id=id, is_admin=False, convenio_rel=[], id_convenio=None)


@needs_db
def test_cancelar_lote_unimed_local_sem_job(db_rollback):
    """Cancelar lote Unimed (período, sem numero_lote) é AÇÃO LOCAL: bloqueia
    itens, reverte conciliações, status final 'Cancelado' — NENHUM job OP14."""
    from models import LoteConvenio, FaturamentoLote, Job
    from routes.lotes import cancelar_lote

    lote = LoteConvenio(id_convenio=3, user_id=1, cod_prestador="TESTE",
                        data_inicio=date(2027, 9, 18), data_fim=date(2027, 10, 17),
                        status="Aberto", numero_lote=None)
    db_rollback.add(lote)
    db_rollback.flush()
    item = FaturamentoLote(detalheId=990009991, id_lote=lote.id_lote, user_id=1,
                           StatusConciliacao="Conciliado", StatusConferencia=0)
    db_rollback.add(item)
    db_rollback.flush()

    req = SimpleNamespace(cod_prestador="TESTE")
    ret = cancelar_lote(lote.id_lote, req, db_rollback, _user_admin())

    assert "localmente" in ret["message"]
    assert lote.status == "Cancelado"
    assert item.StatusConciliacao == "bloqueado"
    jobs_op14 = db_rollback.query(Job).filter(Job.rotina == "14").all()
    assert not any(j.params and str(lote.id_lote) in str(j.params) for j in jobs_op14), \
        "cancelamento Unimed não deve criar job OP14"


@needs_db
def test_cancelar_lote_ipasgo_mantem_op14(db_rollback):
    """Cancelar lote IPASGO (número) mantém o fluxo atual: OP14 + status Cancelando."""
    from models import LoteConvenio, Job
    from routes.lotes import cancelar_lote

    lote = LoteConvenio(id_convenio=6, user_id=1, cod_prestador="TESTE",
                        data_fim=date(2027, 10, 17), status="Aberto", numero_lote=98765)
    db_rollback.add(lote)
    db_rollback.flush()

    req = SimpleNamespace(cod_prestador="TESTE")
    ret = cancelar_lote(lote.id_lote, req, db_rollback, _user_admin())

    assert ret["message"] == "Cancelamento solicitado. Itens bloqueados."
    assert lote.status == "Cancelando"
    job = db_rollback.query(Job).filter(
        Job.rotina == "14", Job.id_convenio == 6).order_by(Job.id.desc()).first()
    assert job is not None, "IPASGO deve seguir criando job OP14"


# ── Reabsorção de itens de lotes cancelados (2026-10-19) ─────────────────────

@needs_db
def test_upsert_reabsorve_item_de_lote_cancelado(db_rollback):
    """Item que ficou preso em lote CANCELADO volta ao lote ativo da importação
    (cenário real do lote 80: chunk 1 inteiro foi 'pulado' contra o lote 66)."""
    from models import LoteConvenio, FaturamentoLote, Job
    from services.guias_sync_service import _upsert_op4_guias

    cancelado = LoteConvenio(id_convenio=3, user_id=1, cod_prestador="TESTE",
                             data_inicio=date(2027, 9, 18), data_fim=date(2027, 10, 17),
                             status="Cancelado")
    ativo = LoteConvenio(id_convenio=3, user_id=1, cod_prestador="TESTE",
                         data_inicio=date(2027, 9, 18), data_fim=date(2027, 10, 17),
                         status="Aberto")
    db_rollback.add_all([cancelado, ativo])
    db_rollback.flush()
    preso = FaturamentoLote(detalheId=990012341, id_lote=cancelado.id_lote, user_id=1,
                            StatusConciliacao="bloqueado", StatusConferencia=0)
    db_rollback.add(preso)
    db_rollback.flush()

    job = db_rollback.query(Job).filter(Job.id == 19152).first()
    counts = {"jobs_processed": 0, "inserted": 0, "updated": 0, "skipped": 0}
    chunk = [{
        "cd_guia": "99001234", "guia": "99RB01", "carteirinha": "222",
        "cod_procedimento": "2250005103",
        "series": [{"seq": 1, "data": "20/09/2027"}],
    }]
    _upsert_op4_guias(db_rollback, job, chunk, counts, id_lote=ativo.id_lote)

    assert counts["updated"] == 1 and counts["inserted"] == 0
    db_rollback.refresh(preso)
    assert preso.id_lote == ativo.id_lote, "item deve migrar para o lote ativo"
    assert preso.StatusConciliacao == "Não Conciliado", "item reabsorvido desbloqueia"


@needs_db
def test_upsert_nao_rouba_item_de_lote_ativo(db_rollback):
    """Item em lote ATIVO permanece no lote dele (idempotência preservada)."""
    from models import LoteConvenio, FaturamentoLote, Job
    from services.guias_sync_service import _upsert_op4_guias

    dono = LoteConvenio(id_convenio=3, user_id=1, cod_prestador="TESTE",
                        data_inicio=date(2027, 9, 18), data_fim=date(2027, 10, 17),
                        status="Aberto")
    outro = LoteConvenio(id_convenio=3, user_id=1, cod_prestador="TESTE",
                         data_inicio=date(2027, 10, 18), data_fim=date(2027, 11, 17),
                         status="Aberto")
    db_rollback.add_all([dono, outro])
    db_rollback.flush()
    item = FaturamentoLote(detalheId=990015671, id_lote=dono.id_lote, user_id=1,
                           StatusConciliacao="Não Conciliado", StatusConferencia=0)
    db_rollback.add(item)
    db_rollback.flush()

    job = db_rollback.query(Job).filter(Job.id == 19152).first()
    counts = {"jobs_processed": 0, "inserted": 0, "updated": 0, "skipped": 0}
    chunk = [{
        "cd_guia": "99001567", "guia": "99RB02", "carteirinha": "333",
        "cod_procedimento": "2250005103",
        "series": [{"seq": 1, "data": "20/09/2027"}],
    }]
    _upsert_op4_guias(db_rollback, job, chunk, counts, id_lote=outro.id_lote)

    assert counts["skipped"] == 1 and counts["updated"] == 0
    db_rollback.refresh(item)
    assert item.id_lote == dono.id_lote, "item de lote ativo não migra"
