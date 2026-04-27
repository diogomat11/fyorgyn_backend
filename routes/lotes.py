from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import LoteConvenio, FaturamentoLote, Job, Carteirinha, LoteAgendamentoItem
from dependencies import get_current_user, get_allowed_convenio_ids
from typing import Optional
from pydantic import BaseModel
from datetime import date

router = APIRouter(
    prefix="/lotes",
    tags=["lotes"]
)

class CreateLoteRequest(BaseModel):
    id_convenio: int
    cod_prestador: str
    data_fim: date

class CancelLoteRequest(BaseModel):
    cod_prestador: str

@router.get("/")
def list_lotes(
    id_convenio: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    allowed_ids = get_allowed_convenio_ids(current_user)
    
    query = db.query(LoteConvenio)
    
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        query = query.filter(LoteConvenio.id_convenio == id_convenio)
    elif allowed_ids:
        query = query.filter(LoteConvenio.id_convenio.in_(allowed_ids))
        
    if status:
        query = query.filter(LoteConvenio.status == status)
        
    total = query.count()
    lotes = query.order_by(LoteConvenio.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"data": lotes, "total": total}

@router.post("/")
def create_lote(
    request: CreateLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and request.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        
    # Create the job for OP13
    import json
    params = json.dumps({
        "cod_prestador": request.cod_prestador,
        "data_fim": request.data_fim.strftime("%d/%m/%Y")
    })
    
    # We create a placeholder Lote in DB so UI can show it's pending
    novo_lote = LoteConvenio(
        id_convenio=request.id_convenio,
        cod_prestador=request.cod_prestador,
        data_fim=request.data_fim,
        status="Processando",
        numero_lote=None # Will be updated by Worker
    )
    db.add(novo_lote)
    db.flush() # get id_lote
    
    new_job = Job(
        id_convenio=request.id_convenio,
        rotina="13", # OP13_criar_lote
        status="pending",
        params=json.dumps({
            "cod_prestador": request.cod_prestador,
            "data_fim": request.data_fim.strftime("%d/%m/%Y"),
            "id_lote_interno": novo_lote.id_lote # Worker can update this row
        })
    )
    db.add(new_job)
    db.commit()
    
    return {"message": "Lote em processamento. Job criado.", "id_lote": novo_lote.id_lote}

@router.post("/{id_lote}/cancelar")
def cancelar_lote(
    id_lote: int,
    request: CancelLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and lote.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão.")
        
    if not lote.numero_lote:
        raise HTTPException(status_code=400, detail="Lote ainda não possui numero_lote gerado (ainda processando?)")
        
    lote.status = "Cancelando"
    
    # Block operations on dependent items + reverse conciliation
    items = db.query(FaturamentoLote).filter(FaturamentoLote.id_lote == lote.id_lote).all()
    for item in items:
        # Se tinha agendamento vinculado, desfazer conciliação
        if item.agendamento_id:
            # Buscar o item do lote de agendamento vinculado e resetar
            lote_ag_items = db.query(LoteAgendamentoItem).filter(
                LoteAgendamentoItem.id_faturamento_lote == item.id
            ).all()
            for lai in lote_ag_items:
                lai.status_conciliacao = "Não Conciliado"
                lai.id_faturamento_lote = None
            item.agendamento_id = None
        item.StatusConciliacao = "bloqueado"
    
    import json
    new_job = Job(
        id_convenio=lote.id_convenio,
        rotina="14", # OP14_cancelar_lote
        status="pending",
        params=json.dumps({
            "cod_prestador": request.cod_prestador,
            "numero_lote": lote.numero_lote,
            "id_lote_interno": lote.id_lote
        })
    )
    db.add(new_job)
    db.commit()
    
    return {"message": "Cancelamento solicitado. Itens bloqueados."}

@router.get("/{id_lote}/faturamentos")
def list_faturamentos_por_lote(
    id_lote: int,
    limit: int = 100,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and lote.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão.")
        
    if not lote.id_lote:
        return {"data": [], "total": 0}
    
    from sqlalchemy.orm import aliased
    cart = aliased(Carteirinha)
    
    query = (
        db.query(FaturamentoLote, cart.paciente.label("nome_beneficiario"))
        .outerjoin(cart, FaturamentoLote.CodigoBeneficiario == cart.codigo_beneficiario)
        .filter(FaturamentoLote.id_lote == lote.id_lote)
    )
    total = query.count()
    rows = query.order_by(FaturamentoLote.created_at.desc()).offset(skip).limit(limit).all()
    
    data = []
    for fat, nome_ben in rows:
        dic = {c.name: getattr(fat, c.name) for c in fat.__table__.columns}
        dic["nome_beneficiario"] = nome_ben or dic.get("CodigoBeneficiario", "")
        data.append(dic)
    
    return {"data": data, "total": total}
