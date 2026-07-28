from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from models import UserConvenioWorkflow, User, Convenio
from dependencies import get_current_user

router = APIRouter(
    prefix="/workflows",
    tags=["Workflows"]
)

class WorkflowCreateRequest(BaseModel):
    user_id: int
    id_convenio: int
    nome_workflow: str
    fluxo_passos: List[Dict[str, Any]]

@router.get("/")
def list_workflows(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lista os workflows customizados de pipeline por usuário e convênio."""
    if not current_user.is_admin:
        query = db.query(UserConvenioWorkflow).filter(UserConvenioWorkflow.user_id == current_user.id)
    else:
        query = db.query(UserConvenioWorkflow)

    workflows = query.order_by(UserConvenioWorkflow.id).all()
    users = {u.id: u.username for u in db.query(User).all()}
    convs = {c.id_convenio: c.nome for c in db.query(Convenio).all()}

    return [
        {
            "id": w.id,
            "user_id": w.user_id,
            "username": users.get(w.user_id, "Desconhecido"),
            "id_convenio": w.id_convenio,
            "nome_convenio": convs.get(w.id_convenio, "Desconhecido"),
            "nome_workflow": w.nome_workflow,
            "fluxo_passos": w.fluxo_passos or [],
            "created_at": w.created_at,
            "updated_at": w.updated_at
        }
        for w in workflows
    ]

@router.post("/")
def create_or_update_workflow(
    req: WorkflowCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria ou atualiza o workflow dinâmico em cadeia para um (user_id, id_convenio)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem gerenciar workflows.")

    existing = db.query(UserConvenioWorkflow).filter(
        UserConvenioWorkflow.user_id == req.user_id,
        UserConvenioWorkflow.id_convenio == req.id_convenio
    ).first()

    passos_dict = req.fluxo_passos

    if existing:
        existing.nome_workflow = req.nome_workflow
        existing.fluxo_passos = passos_dict
        db.commit()
        db.refresh(existing)
        return {"message": "Workflow atualizado com sucesso.", "id": existing.id}

    new_wf = UserConvenioWorkflow(
        user_id=req.user_id,
        id_convenio=req.id_convenio,
        nome_workflow=req.nome_workflow,
        fluxo_passos=passos_dict
    )
    db.add(new_wf)
    db.commit()
    db.refresh(new_wf)
    return {"message": "Workflow criado com sucesso.", "id": new_wf.id}

@router.delete("/{workflow_id}")
def delete_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Remove um workflow dinâmico."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")

    wf = db.query(UserConvenioWorkflow).filter(UserConvenioWorkflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow não encontrado.")

    db.delete(wf)
    db.commit()
    return {"message": "Workflow removido com sucesso."}
