"""
Rotas do módulo Recurso de Glosa (IPASGO).

- Lotes do convênio IPASGO com protocolo (capa) e agregados dos itens;
- Itens importados pela OP10 com filtros (guia, paciente, data, status);
- Catálogo de justificativas (padrão motivos_faltas) e aplicação em massa;
- Envio das justificativas ao portal via job da OP16.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, get_effective_user_id
from models import (
    FaturamentoLote,
    LoteConvenio,
    RecursoGlosaItem,
    UserConvenio,
    JustificativaGlosa,
)

router = APIRouter(
    prefix="/recurso-glosa",
    tags=["Recurso de Glosa"]
)

ID_CONVENIO_IPASGO = 6

STATUS_PENDENTE = "Pendente"
STATUS_JUSTIFICATIVA = "Justificativa Definida"
STATUS_ENVIADO = "Enviado"

ROTINA_IMPORTAR = "op10_recurso_glosa"
ROTINA_ENVIAR = "op16_enviar_recurso_glosa"


def _query_lotes_tenant(db: Session, current_user):
    """Lotes do IPASGO visíveis para o usuário (mesma guarda do router /lotes)."""
    query = db.query(LoteConvenio).filter(LoteConvenio.id_convenio == ID_CONVENIO_IPASGO)
    if not current_user.is_admin:
        query = query.filter(LoteConvenio.user_id == current_user.id)
    return query


def _lote_do_usuario(db: Session, current_user, id_lote: int) -> LoteConvenio:
    lote = _query_lotes_tenant(db, current_user).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado para este usuário.")
    return lote


# ══════════════════════════════ Lotes ══════════════════════════════

@router.get("/lotes")
def list_lotes(
    limit: int = 100,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    lotes = (
        _query_lotes_tenant(db, current_user)
        .order_by(LoteConvenio.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    ids = [l.id_lote for l in lotes]

    # Agregados dos itens em uma passada (counts por status + somas de valores)
    agregados = {}
    if ids:
        status_rows = db.query(
            RecursoGlosaItem.id_lote,
            RecursoGlosaItem.status,
            func.count(RecursoGlosaItem.id),
            func.coalesce(func.sum(RecursoGlosaItem.valor_unitario_glosado * RecursoGlosaItem.quantidade_glosada), 0.0),
            func.coalesce(func.sum(RecursoGlosaItem.valor_recurso), 0.0),
        ).filter(RecursoGlosaItem.id_lote.in_(ids)).group_by(
            RecursoGlosaItem.id_lote, RecursoGlosaItem.status
        ).all()

        for id_lote, status, count, glosado, recurso in status_rows:
            agg = agregados.setdefault(id_lote, {
                "total_itens": 0, "com_justificativa": 0, "enviados": 0,
                "valor_glosado": 0.0, "valor_recurso": 0.0,
            })
            agg["total_itens"] += count
            agg["valor_glosado"] += float(glosado or 0)
            agg["valor_recurso"] += float(recurso or 0)
            if status == STATUS_ENVIADO:
                agg["enviados"] += count
                agg["com_justificativa"] += count
            elif status == STATUS_JUSTIFICATIVA:
                agg["com_justificativa"] += count

    data = []
    for lote in lotes:
        agg = agregados.get(lote.id_lote, {
            "total_itens": 0, "com_justificativa": 0, "enviados": 0,
            "valor_glosado": 0.0, "valor_recurso": 0.0,
        })
        data.append({
            "id_lote": lote.id_lote,
            "numero_lote": lote.numero_lote,
            "protocolo": lote.protocolo,
            "cod_prestador": lote.cod_prestador,
            "status": lote.status,
            "data_inicio": lote.data_inicio,
            "data_fim": lote.data_fim,
            "created_at": lote.created_at,
            **agg,
        })
    return {"data": data, "total": len(data)}


class ProtocoloUpdate(BaseModel):
    protocolo: str


@router.put("/lotes/{id_lote}/protocolo")
def update_protocolo(
    id_lote: int,
    req: ProtocoloUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    protocolo = req.protocolo.strip()
    if not protocolo or not protocolo.isdigit():
        raise HTTPException(status_code=400, detail="Informe o protocolo numérico do lote no portal (ex.: 895854).")

    lote = _lote_do_usuario(db, current_user, id_lote)
    lote.protocolo = protocolo
    # Mantém os itens já importados coerentes com a capa
    db.query(RecursoGlosaItem).filter(
        RecursoGlosaItem.id_lote == lote.id_lote
    ).update({"protocolo": protocolo}, synchronize_session=False)
    db.commit()
    return {"id_lote": lote.id_lote, "protocolo": lote.protocolo}


# ══════════════════════════════ Itens ══════════════════════════════

@router.get("/itens")
def list_itens(
    id_lote: int,
    guia: Optional[str] = None,
    paciente: Optional[str] = None,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    status: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    _lote_do_usuario(db, current_user, id_lote)

    query = (
        db.query(RecursoGlosaItem, FaturamentoLote.dataRealizacao)
        .outerjoin(
            FaturamentoLote,
            RecursoGlosaItem.id_faturamento_lote == FaturamentoLote.id,
        )
        .filter(RecursoGlosaItem.id_lote == id_lote)
    )

    if guia and guia.strip():
        query = query.filter(RecursoGlosaItem.guia.ilike(f"%{guia.strip()}%"))
    if paciente and paciente.strip():
        termo = f"%{paciente.strip()}%"
        query = query.filter(or_(
            RecursoGlosaItem.nome_segurado.ilike(termo),
            RecursoGlosaItem.codigo_segurado.ilike(termo),
        ))
    if data_inicio:
        query = query.filter(FaturamentoLote.dataRealizacao >= data_inicio)
    if data_fim:
        query = query.filter(FaturamentoLote.dataRealizacao <= data_fim)
    if status and status.strip():
        query = query.filter(RecursoGlosaItem.status == status.strip())

    total = query.count()
    rows = (
        query.order_by(RecursoGlosaItem.guia, RecursoGlosaItem.chave)
        .offset(skip)
        .limit(limit)
        .all()
    )

    data = []
    for item, data_realizacao in rows:
        data.append({
            "id": item.id,
            "id_lote": item.id_lote,
            "chave": item.chave,
            "chave_ut_serv": item.chave_ut_serv,
            "codigo_segurado": item.codigo_segurado,
            "nome_segurado": item.nome_segurado,
            "guia": item.guia,
            "codigo_amb": item.codigo_amb,
            "descricao_servico": item.descricao_servico,
            "quantidade_glosada": item.quantidade_glosada,
            "valor_unitario_glosado": item.valor_unitario_glosado,
            "valor_recurso": item.valor_recurso,
            "justificativa_portal": item.justificativa_portal,
            "justificativa_id": item.justificativa_id,
            "justificativa_texto": item.justificativa_texto,
            "status": item.status,
            "data_envio": item.data_envio,
            "data_realizacao": data_realizacao,
        })
    return {"data": data, "total": total}


class AplicarJustificativaRequest(BaseModel):
    id_lote: int
    item_ids: Optional[List[int]] = None  # ausente + todos=False -> nenhum; informe ids OU todos
    todos: bool = False  # aplica a TODOS os itens não enviados do lote
    justificativa_id: int


@router.post("/itens/justificativa")
def aplicar_justificativa(
    req: AplicarJustificativaRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    _lote_do_usuario(db, current_user, req.id_lote)

    efetivo_id = get_effective_user_id(current_user)
    justificativa = db.query(JustificativaGlosa).filter(
        JustificativaGlosa.id == req.justificativa_id,
        JustificativaGlosa.status == "Ativo",
        or_(JustificativaGlosa.user_id == efetivo_id, JustificativaGlosa.user_id.is_(None)),
    ).first()
    if not justificativa:
        raise HTTPException(status_code=404, detail="Justificativa não encontrada ou inativa.")

    query = db.query(RecursoGlosaItem).filter(RecursoGlosaItem.id_lote == req.id_lote)
    if req.todos or not req.item_ids:
        # Em massa: todos os itens ainda não enviados do lote
        query = query.filter(RecursoGlosaItem.status != STATUS_ENVIADO)
    else:
        query = query.filter(RecursoGlosaItem.id.in_(req.item_ids))
        query = query.filter(RecursoGlosaItem.status != STATUS_ENVIADO)

    itens = query.all()
    if not itens:
        raise HTTPException(status_code=400, detail="Nenhum item elegível para receber a justificativa.")

    for item in itens:
        item.justificativa_id = justificativa.id
        item.justificativa_texto = justificativa.descricao
        item.status = STATUS_JUSTIFICATIVA
    db.commit()

    return {"message": "Justificativa aplicada.", "itens_atualizados": len(itens)}


# ════════════════════════ Justificativas (catálogo) ════════════════════════

class JustificativaCreate(BaseModel):
    descricao: str

class JustificativaUpdate(BaseModel):
    descricao: Optional[str] = None
    status: Optional[str] = None


@router.get("/justificativas")
def list_justificativas(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    efetivo_id = get_effective_user_id(current_user)
    return (
        db.query(JustificativaGlosa)
        .filter(
            JustificativaGlosa.status == "Ativo",
            or_(JustificativaGlosa.user_id == efetivo_id, JustificativaGlosa.user_id.is_(None)),
        )
        .order_by(JustificativaGlosa.descricao)
        .all()
    )


@router.post("/justificativas")
def create_justificativa(
    req: JustificativaCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    descricao = req.descricao.strip()
    if len(descricao) < 10:
        raise HTTPException(status_code=400, detail="Descreva a justificativa (mínimo 10 caracteres).")

    justificativa = JustificativaGlosa(
        descricao=descricao,
        user_id=get_effective_user_id(current_user),
    )
    db.add(justificativa)
    db.commit()
    db.refresh(justificativa)
    return justificativa


@router.put("/justificativas/{justificativa_id}")
def update_justificativa(
    justificativa_id: int,
    req: JustificativaUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    efetivo_id = get_effective_user_id(current_user)
    justificativa = db.query(JustificativaGlosa).filter(
        JustificativaGlosa.id == justificativa_id,
        or_(JustificativaGlosa.user_id == efetivo_id, JustificativaGlosa.user_id.is_(None)),
    ).first()
    if not justificativa:
        raise HTTPException(status_code=404, detail="Justificativa não encontrada.")

    if req.descricao is not None:
        descricao = req.descricao.strip()
        if len(descricao) < 10:
            raise HTTPException(status_code=400, detail="Descreva a justificativa (mínimo 10 caracteres).")
        justificativa.descricao = descricao
    if req.status is not None:
        justificativa.status = req.status
    db.commit()
    db.refresh(justificativa)
    return justificativa


@router.delete("/justificativas/{justificativa_id}")
def delete_justificativa(
    justificativa_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    efetivo_id = get_effective_user_id(current_user)
    justificativa = db.query(JustificativaGlosa).filter(
        JustificativaGlosa.id == justificativa_id,
        JustificativaGlosa.user_id == efetivo_id,
    ).first()
    if not justificativa:
        raise HTTPException(status_code=404, detail="Justificativa não encontrada.")
    justificativa.status = "Inativo"
    db.commit()
    return {"message": "Justificativa desativada com sucesso."}


# ════════════════════════ Envio (job OP16) ════════════════════════

class EnviarRequest(BaseModel):
    item_ids: Optional[List[int]] = None  # ausente -> todos os itens prontos (Justificativa Definida)


def _montar_servicos_envio(itens: list) -> list:
    """
    Converte os itens do banco no payload 'servicos' da OP16. valor_recurso
    nulo cai para valor_unitario_glosado x quantidade_glosada.
    """
    def _valor(v):
        numero = round(float(v or 0), 2)
        return int(numero) if numero.is_integer() else numero

    servicos = []
    for item in itens:
        quantidade = item.quantidade_glosada or 1
        valor_recurso = item.valor_recurso
        if valor_recurso is None:
            valor_recurso = round(float(item.valor_unitario_glosado or 0) * quantidade, 2)
        servicos.append({
            "chave": item.chave,
            "chave_ut_serv": item.chave_ut_serv or item.chave,
            "quantidade_glosada": quantidade,
            "valor_unitario_glosado": item.valor_unitario_glosado or 0,
            "valor_recurso": _valor(valor_recurso),
            "justificativa": (item.justificativa_texto or "").strip(),
        })
    return servicos


@router.post("/lotes/{id_lote}/enviar")
def enviar_recurso(
    id_lote: int,
    req: EnviarRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    lote = _lote_do_usuario(db, current_user, id_lote)

    protocolo = (lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(
            status_code=400,
            detail="Informe o protocolo do lote antes de enviar (coluna Protocolo).",
        )

    query = db.query(RecursoGlosaItem).filter(
        RecursoGlosaItem.id_lote == lote.id_lote,
        RecursoGlosaItem.status == STATUS_JUSTIFICATIVA,
    )
    if req.item_ids:
        query = query.filter(RecursoGlosaItem.id.in_(req.item_ids))

    itens = query.all()
    if not itens:
        raise HTTPException(
            status_code=400,
            detail="Nenhum item pronto para envio. Vincule justificativas aos itens primeiro.",
        )

    servicos = _montar_servicos_envio(itens)
    sem_justificativa = [s for s in servicos if not s["justificativa"]]
    if sem_justificativa:
        raise HTTPException(
            status_code=400,
            detail=f"{len(sem_justificativa)} item(ns) com justificativa vazia. Revincule a justificativa antes de enviar.",
        )

    # Credenciais do IPASGO (mesma injeção do fluxo standalone do POST /jobs/)
    efetivo_id = get_effective_user_id(current_user)
    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == efetivo_id,
        UserConvenio.id_convenio == ID_CONVENIO_IPASGO,
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(
            status_code=400,
            detail="Credenciais não cadastradas para o convênio IPASGO. Cadastre em Credenciais Portais.",
        )

    cod_prestador = (lote.cod_prestador or uconv.cod_prestador or "").strip()

    from services.job_service import MY_WEBHOOK_URL, _send_jobs_to_worker

    p_dict = {
        "protocolo": protocolo,
        "capa": protocolo,
        "id_lote_interno": lote.id_lote,
        "servicos": servicos,
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "cod_prestador": cod_prestador,
        "codigoPrestador": cod_prestador,
        "webhook_url": MY_WEBHOOK_URL,
    }

    job_payload = {
        "carteirinha_id": None,
        "id_convenio": ID_CONVENIO_IPASGO,
        "user_id": lote.user_id or current_user.id,
        "rotina": ROTINA_ENVIAR,
        "priority": 0,
        "params": p_dict,
        "max_attempts": 3,
    }

    _send_jobs_to_worker([job_payload], db=db)

    return {
        "message": "Envio de recurso de glosa acionado.",
        "id_lote": lote.id_lote,
        "protocolo": protocolo,
        "servicos": len(servicos),
    }
