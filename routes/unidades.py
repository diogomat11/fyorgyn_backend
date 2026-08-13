from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import Unidade, Agendamento
from dependencies import get_current_user, get_effective_user_id
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
    status: Optional[str] = "Ativo"

    class Config:
        from_attributes = True

class UnidadeCreateRequest(BaseModel):
    id_unidade: Optional[int] = None
    nome: str
    status: Optional[str] = "Ativo"

class UnidadeUpdateRequest(BaseModel):
    nome: Optional[str] = None
    status: Optional[str] = None

@router.get("", response_model=List[UnidadeResponse])
@router.get("/", response_model=List[UnidadeResponse])
def list_unidades(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Retorna as unidades da clínica vinculadas ao usuário logado."""
    target_uid = get_effective_user_id(current_user)
    
    query = db.query(Unidade)
    if not current_user.is_admin:
        query = query.filter(Unidade.user_id == target_uid)
        
    unidades = query.order_by(Unidade.id_unidade).all()

    # Fallback se a tabela estiver completamente sem cadastros para o user_id
    if not unidades:
        ag_unids_query = db.query(Agendamento.id_unidade).distinct()
        if not current_user.is_admin:
            ag_unids_query = ag_unids_query.filter(Agendamento.user_id == target_uid)
        ag_unids = [x[0] for x in ag_unids_query.all() if x[0] is not None]

        unidades = [
            Unidade(id=uid, id_unidade=uid, nome=f"Unidade #{uid}", user_id=target_uid, status="Ativo")
            for uid in ag_unids
        ]

    return unidades

@router.post("", response_model=UnidadeResponse)
@router.post("/", response_model=UnidadeResponse)
def create_unidade(
    request: UnidadeCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria uma nova unidade para a clínica (Gestor ou Admin)."""
    user_perfil = getattr(current_user, "perfil", "gestor") or "gestor"
    if not current_user.is_admin and user_perfil not in ["gestor", "supervisor"]:
        raise HTTPException(status_code=403, detail="Apenas gestores podem cadastrar unidades.")
    
    target_uid = get_effective_user_id(current_user)
    
    # Auto-generate id_unidade if not provided
    if request.id_unidade is None:
        max_id = db.query(Unidade).filter(Unidade.user_id == target_uid).order_by(Unidade.id_unidade.desc()).first()
        next_id = (max_id.id_unidade + 1) if max_id else 1
    else:
        next_id = request.id_unidade
        
    # Check duplicate
    existing = db.query(Unidade).filter(Unidade.user_id == target_uid, Unidade.id_unidade == next_id).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Já existe uma unidade cadastrada com o código #{next_id}.")
        
    new_unid = Unidade(
        id_unidade=next_id,
        nome=request.nome.strip(),
        user_id=target_uid,
        status=request.status or "Ativo"
    )
    db.add(new_unid)
    db.commit()
    db.refresh(new_unid)
    return new_unid

@router.put("/{id_unidade}", response_model=UnidadeResponse)
def update_unidade(
    id_unidade: int,
    request: UnidadeUpdateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Editar nome ou status de uma unidade (Gestor ou Admin)."""
    user_perfil = getattr(current_user, "perfil", "gestor") or "gestor"
    if not current_user.is_admin and user_perfil not in ["gestor", "supervisor"]:
        raise HTTPException(status_code=403, detail="Apenas gestores podem editar unidades.")
        
    target_uid = get_effective_user_id(current_user)
    
    unid = db.query(Unidade).filter(Unidade.id_unidade == id_unidade)
    if not current_user.is_admin:
        unid = unid.filter(Unidade.user_id == target_uid)
    unid = unid.first()
    
    if not unid:
        raise HTTPException(status_code=404, detail="Unidade não encontrada.")
        
    if request.nome and request.nome.strip():
        unid.nome = request.nome.strip()
    if request.status:
        unid.status = request.status
        
    db.commit()
    db.refresh(unid)
    return unid

@router.patch("/{id_unidade}/status")
def toggle_unidade_status(
    id_unidade: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Alterna o status de uma unidade (Ativo / Inativo)."""
    user_perfil = getattr(current_user, "perfil", "gestor") or "gestor"
    if not current_user.is_admin and user_perfil not in ["gestor", "supervisor"]:
        raise HTTPException(status_code=403, detail="Apenas gestores podem alterar status de unidades.")
        
    target_uid = get_effective_user_id(current_user)
    
    unid = db.query(Unidade).filter(Unidade.id_unidade == id_unidade)
    if not current_user.is_admin:
        unid = unid.filter(Unidade.user_id == target_uid)
    unid = unid.first()
    
    if not unid:
        raise HTTPException(status_code=404, detail="Unidade não encontrada.")
        
    unid.status = "Inativo" if unid.status == "Ativo" else "Ativo"
    db.commit()
    return {"status": "success", "id_unidade": unid.id_unidade, "novo_status": unid.status}
