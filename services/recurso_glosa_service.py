"""
Serviço de consumo dos resultados das OPs de Recurso de Glosa do IPASGO.

- OP10 (op10_recurso_glosa): upsert dos itens importados do portal em
  recurso_glosa_itens (chave única id_lote+chave), resolvendo os vínculos:
    - codigo_segurado -> carteirinhas.codigo_beneficiario (carteirinha_id);
    - guia (sem zeros à esquerda) -> base_guias (base_guia_id);
    - fatura -> faturamento_lotes (id_faturamento_lote, por lote+guia+procedimento).
- OP16 (op16_enviar_recurso_glosa): marca os itens enviados (status Enviado)
  e reaproveita a OP10 encadeada para atualizar o texto do portal.

Re-importações preservam os campos definidos pelo usuário (justificativa,
status, data_envio) — apenas os dados do portal são atualizados.
"""
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import (
    Carteirinha,
    BaseGuia,
    FaturamentoLote,
    LoteConvenio,
    RecursoGlosaItem,
)

STATUS_PENDENTE = "Pendente"
STATUS_JUSTIFICATIVA = "Justificativa Definida"
STATUS_ENVIADO = "Enviado"

# Chaves que identificam o envelope das OPs 10/16 dentro do wrapper do worker
_MARCADORES_OPS = ("itens", "capa", "servicos_enviados")


def _desempacotar_resultado(res_data):
    """
    O dispatcher/server envelopa o retorno das OPs como {status, data, job_id}
    (job 23632: os 2.393 itens estavam em result_data['data']['itens']).
    Alguns caminhos gravam o retorno cru da OP — cobre ambos.
    """
    inner = res_data.get("data") if isinstance(res_data, dict) else None
    if isinstance(inner, dict) and any(marcador in inner for marcador in _MARCADORES_OPS):
        return inner
    return res_data


def _parse_params(job) -> dict:
    params = job.params
    if isinstance(params, str):
        try:
            return json.loads(params)
        except Exception:
            return {}
    return params or {}


def _resolver_carteirinhas(db: Session, itens: list, user_id) -> dict:
    """
    Map codigo_segurado -> carteirinha_id. Tenta match exato no
    codigo_beneficiario e, como fallback, sem o sufixo de dígito verificador
    (ex.: "105770-7" -> "105770"), mesmo critério de tenant do bulk de guias.
    """
    codigos = set()
    for item in itens:
        cod = str(item.get("codigo_segurado") or "").strip()
        if cod:
            codigos.add(cod)
            if "-" in cod:
                codigos.add(cod.split("-", 1)[0])

    mapa = {}
    if not codigos:
        return mapa

    query = db.query(Carteirinha).filter(Carteirinha.codigo_beneficiario.in_(list(codigos)))
    if user_id is not None:
        query = query.filter(Carteirinha.user_id == user_id)
    for cart in query.all():
        mapa[cart.codigo_beneficiario] = cart.id

    resolved = {}
    for item in itens:
        cod = str(item.get("codigo_segurado") or "").strip()
        if not cod:
            continue
        resolved[cod] = mapa.get(cod) or mapa.get(cod.split("-", 1)[0])
    return resolved


def _resolver_base_guias(db: Session, itens: list) -> dict:
    """
    Map (guia, codigo_amb) -> base_guia_id. Tenta o par (guia + codigo_terapia)
    e cai para guia isolada quando o procedimento não casa.
    """
    guias = {item.get("guia") for item in itens if item.get("guia")}
    if not guias:
        return {}

    base = db.query(BaseGuia).filter(BaseGuia.guia.in_(list(guias))).all()

    por_guia_terapia = {}
    por_guia = {}
    for g in base:
        por_guia.setdefault(g.guia, g.id)
        if g.codigo_terapia:
            por_guia_terapia.setdefault((g.guia, g.codigo_terapia), g.id)

    resolved = {}
    for item in itens:
        guia = item.get("guia")
        if not guia:
            continue
        resolved[guia] = por_guia_terapia.get((guia, item.get("codigo_amb"))) or por_guia.get(guia)
    return resolved


def _resolver_faturamentos(db: Session, id_lote: int, itens: list) -> dict:
    """
    Map guia -> id_faturamento_lote dentro do mesmo lote. Prioriza o match
    exato (Guia + cod_procedimento_fat = codigo_amb) e cai para guia isolada.
    """
    guias = {item.get("guia") for item in itens if item.get("guia")}
    if not guias or not id_lote:
        return {}

    fats = db.query(FaturamentoLote).filter(
        FaturamentoLote.id_lote == id_lote,
        FaturamentoLote.Guia.in_(list(guias)),
    ).all()

    por_guia_proc = {}
    por_guia = {}
    for f in fats:
        por_guia.setdefault(f.Guia, f.id)
        if f.cod_procedimento_fat:
            por_guia_proc.setdefault((f.Guia, f.cod_procedimento_fat), f.id)

    resolved = {}
    for item in itens:
        guia = item.get("guia")
        if not guia:
            continue
        resolved[guia] = por_guia_proc.get((guia, item.get("codigo_amb"))) or por_guia.get(guia)
    return resolved


def upsert_itens_recurso_glosa(db: Session, job, res_data: dict, synced_counts: dict) -> None:
    """
    Consome o envelope da OP10: {"protocolo", "id_lote_interno",
    "numero_paginas", "total_itens", "itens": [...]}. Itens novos entram como
    Pendente; existentes têm apenas os dados do portal atualizados.
    """
    if not isinstance(res_data, dict):
        print(f"[SYNC] Job {job.id} OP10: resultado inesperado ({type(res_data)}).")
        job.result_consumed = True
        synced_counts["skipped"] = synced_counts.get("skipped", 0) + 1
        return

    res_data = _desempacotar_resultado(res_data)

    itens = res_data.get("itens") or []
    protocolo = str(res_data.get("protocolo") or "").strip()
    p_params = _parse_params(job)
    id_lote = res_data.get("id_lote_interno") or p_params.get("id_lote_interno")

    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first() if id_lote else None
    if not lote:
        print(f"[SYNC] Job {job.id} OP10: lote interno {id_lote} não encontrado — itens não persistidos.")
        job.result_consumed = True
        synced_counts["skipped"] = synced_counts.get("skipped", 0) + 1
        db.commit()
        return

    # O lote é a âncora do tenant (sub-usuários podem disparar o job)
    user_id = lote.user_id or job.user_id

    if protocolo and not lote.protocolo:
        lote.protocolo = protocolo

    carteirinha_map = _resolver_carteirinhas(db, itens, user_id)
    base_guia_map = _resolver_base_guias(db, itens)
    faturamento_map = _resolver_faturamentos(db, lote.id_lote, itens)

    existentes = {}
    if itens:
        chaves = [i.get("chave") for i in itens if i.get("chave") is not None]
        if chaves:
            rows = db.query(RecursoGlosaItem).filter(
                RecursoGlosaItem.id_lote == lote.id_lote,
                RecursoGlosaItem.chave.in_(chaves),
            ).all()
            existentes = {row.chave: row for row in rows}

    now_utc = datetime.now(timezone.utc)
    inseridos = atualizados = 0

    for item in itens:
        chave = item.get("chave")
        if chave is None:
            continue

        guia = str(item.get("guia") or "").strip() or None
        existente = existentes.get(chave)

        if existente:
            # Re-importação: atualiza apenas os dados vindos do portal
            existente.chave_ut_serv = item.get("chave_ut_serv") or existente.chave_ut_serv
            existente.quantidade_glosada = item.get("quantidade_glosada") or existente.quantidade_glosada
            existente.valor_unitario_glosado = (
                item.get("valor_unitario_glosado")
                if item.get("valor_unitario_glosado") is not None
                else existente.valor_unitario_glosado
            )
            existente.valor_recurso = (
                item.get("valor_recurso")
                if item.get("valor_recurso") is not None
                else existente.valor_recurso
            )
            existente.codigo_segurado = item.get("codigo_segurado") or existente.codigo_segurado
            existente.nome_segurado = item.get("nome_segurado") or existente.nome_segurado
            existente.guia = guia or existente.guia
            existente.codigo_amb = item.get("codigo_amb") or existente.codigo_amb
            existente.descricao_servico = item.get("descricao_servico") or existente.descricao_servico
            existente.justificativa_portal = item.get("justificativa") or existente.justificativa_portal
            existente.carteirinha_id = carteirinha_map.get(item.get("codigo_segurado")) or existente.carteirinha_id
            existente.base_guia_id = (base_guia_map.get(guia) if guia else None) or existente.base_guia_id
            existente.id_faturamento_lote = (faturamento_map.get(guia) if guia else None) or existente.id_faturamento_lote
            existente.updated_at = now_utc
            atualizados += 1
        else:
            db.add(RecursoGlosaItem(
                id_lote=lote.id_lote,
                user_id=user_id,
                protocolo=protocolo or lote.protocolo,
                chave=chave,
                chave_ut_serv=item.get("chave_ut_serv"),
                quantidade_glosada=item.get("quantidade_glosada"),
                valor_unitario_glosado=item.get("valor_unitario_glosado"),
                valor_recurso=item.get("valor_recurso"),
                codigo_segurado=item.get("codigo_segurado"),
                nome_segurado=item.get("nome_segurado"),
                carteirinha_id=carteirinha_map.get(item.get("codigo_segurado")),
                guia=guia,
                base_guia_id=base_guia_map.get(guia) if guia else None,
                codigo_amb=item.get("codigo_amb"),
                descricao_servico=item.get("descricao_servico"),
                id_faturamento_lote=faturamento_map.get(guia) if guia else None,
                justificativa_portal=item.get("justificativa"),
                status=STATUS_PENDENTE,
            ))
            inseridos += 1

    db.commit()
    print(f"[SYNC] Job {job.id} OP10: {inseridos} itens inseridos, {atualizados} atualizados (lote {lote.id_lote}).")

    job.result_consumed = True
    synced_counts["jobs_processed"] = synced_counts.get("jobs_processed", 0) + 1
    db.commit()


def marcar_itens_enviados(db: Session, job, res_data: dict, synced_counts: dict) -> None:
    """
    Consome o envelope da OP16: marca os serviços enviados como Enviado e,
    quando a OP10 encadeada trouxe itens, atualiza os textos do portal
    (upsert preserva o status Enviado recém-gravado).
    """
    if not isinstance(res_data, dict):
        job.result_consumed = True
        synced_counts["skipped"] = synced_counts.get("skipped", 0) + 1
        return

    res_data = _desempacotar_resultado(res_data)

    protocolo = str(res_data.get("capa") or res_data.get("protocolo") or "").strip()
    p_params = _parse_params(job)
    id_lote = res_data.get("id_lote_interno") or p_params.get("id_lote_interno")

    lote = None
    if id_lote:
        lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote and protocolo:
        lote = db.query(LoteConvenio).filter(
            LoteConvenio.protocolo == protocolo,
            LoteConvenio.id_convenio == 6,
        ).first()

    now_utc = datetime.now(timezone.utc)
    marcados = 0
    if lote:
        chaves = [
            int(s.get("chave")) for s in (p_params.get("servicos") or [])
            if str(s.get("chave", "")).strip().lstrip("-").isdigit()
        ]
        if chaves:
            rows = db.query(RecursoGlosaItem).filter(
                RecursoGlosaItem.id_lote == lote.id_lote,
                RecursoGlosaItem.chave.in_(chaves),
            ).all()
            for row in rows:
                row.status = STATUS_ENVIADO
                row.data_envio = now_utc
                row.updated_at = now_utc
                marcados += 1
            db.commit()

    print(f"[SYNC] Job {job.id} OP16: {marcados} itens marcados como Enviado.")

    # OP10 encadeada (pós-envio): atualiza a visão do portal preservando status
    op10_data = res_data.get("op10_data")
    if isinstance(op10_data, dict) and op10_data.get("itens"):
        try:
            upsert_itens_recurso_glosa(db, job, op10_data, synced_counts)
            return
        except Exception as e:
            print(f"[SYNC] Job {job.id} OP16: falha ao consumir OP10 encadeada: {e}")
            db.rollback()

    job.result_consumed = True
    synced_counts["jobs_processed"] = synced_counts.get("jobs_processed", 0) + 1
    db.commit()
