from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Unidade
from dependencies import get_current_user
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(
    prefix="/unidades",
    tags=["Unidades"]
)

class UnidadeResponse(BaseModel):
    id: int
    id_unidade: int
    nome: str
    user_id: int
    status: str

    class Config:
        from_attributes = True

@router.get("/", response_model=List[UnidadeResponse])
def list_unidades(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Retorna a listagem de unidades do usuário logado (multi-tenant estrito).
    """
    unidades = db.query(Unidade).filter(
        Unidade.user_id == current_user.id,
        Unidade.status == "ativo"
    ).order_by(Unidade.id_unidade).all()

    return unidades
