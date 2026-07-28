from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from pydantic import BaseModel
from typing import Optional
from models import MotivoFalta
from dependencies import get_current_user

router = APIRouter(
    prefix="/motivos-faltas",
    tags=["Motivos de Falta"]
)

class MotivoFaltaCreate(BaseModel):
    descricao: str
    id_mapeado: Optional[int] = None
    status: str = "Ativo"
    tipo: Optional[str] = None
    anexo: str = "NÃO"

class MotivoFaltaUpdate(BaseModel):
    descricao: Optional[str] = None
    id_mapeado: Optional[int] = None
    status: Optional[str] = None
    tipo: Optional[str] = None
    anexo: Optional[str] = None

@router.get("/")
def list_motivos(
    tipo: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(MotivoFalta).filter(MotivoFalta.status == "Ativo")
    if tipo:
        query = query.filter(MotivoFalta.tipo == tipo)
    return query.order_by(MotivoFalta.tipo, MotivoFalta.descricao).all()

@router.get("/all")
def list_all_motivos(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    return db.query(MotivoFalta).order_by(MotivoFalta.id).all()

@router.post("/")
def create_motivo(
    req: MotivoFaltaCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    motivo = MotivoFalta(
        descricao=req.descricao,
        user_id=current_user.id,
        id_mapeado=req.id_mapeado,
        status=req.status,
        tipo=req.tipo,
        anexo=req.anexo
    )
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return motivo

@router.put("/{motivo_id}")
def update_motivo(
    motivo_id: int,
    req: MotivoFaltaUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    motivo = db.query(MotivoFalta).filter(MotivoFalta.id == motivo_id).first()
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo de falta não encontrado.")
    if req.descricao is not None:
        motivo.descricao = req.descricao
    if req.id_mapeado is not None:
        motivo.id_mapeado = req.id_mapeado
    if req.status is not None:
        motivo.status = req.status
    if req.tipo is not None:
        motivo.tipo = req.tipo
    if req.anexo is not None:
        motivo.anexo = req.anexo
    db.commit()
    db.refresh(motivo)
    return motivo

@router.delete("/{motivo_id}")
def delete_motivo(
    motivo_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    motivo = db.query(MotivoFalta).filter(MotivoFalta.id == motivo_id).first()
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo de falta não encontrado.")
    motivo.status = "Inativo"
    db.commit()
    return {"message": "Motivo de falta desativado com sucesso."}
