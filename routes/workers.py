from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from pydantic import BaseModel
from typing import Optional, Dict, Any
from services import worker_service

router = APIRouter(
    prefix="/workers",
    tags=["workers"],
    responses={404: {"description": "Not found"}},
)

class HeartbeatSchema(BaseModel):
    hostname: str
    status: str
    current_job_id: Optional[int] = None
    meta: Optional[Dict[str, Any]] = None

@router.post("/heartbeat")
def heartbeat(data: HeartbeatSchema, db: Session = Depends(get_db)):
    """
    Endpoint for workers to send heartbeat.
    Returns instructions (commands).
    """
    result = worker_service.register_heartbeat(
        db, 
        hostname=data.hostname, 
        status=data.status, 
        current_job_id=data.current_job_id, 
        meta=data.meta
    )
    return result

from dependencies import get_current_user

@router.get("/")
def list_workers(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """
    List all registered workers.
    """
    return worker_service.get_all_workers(db)

@router.post("/{worker_id}/restart")
def restart_worker(worker_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """
    Queue a restart command for a worker.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    worker = worker_service.queue_restart_command(db, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return {"message": "Restart command queued", "worker": worker.hostname}


class RegisterWorkerSchema(BaseModel):
    api_key: str
    hostname: Optional[str] = None
    descricao: Optional[str] = None

@router.post("/register")
def register_worker_by_api_key(data: RegisterWorkerSchema, db: Session = Depends(get_db)):
    """
    Chamado pela interface GUI do Worker local ao conectar ou ao inserir a chave API.
    Gera dinamicamente uma nova worker_key única a cada conexão para garantir que a chave nunca seja estática.
    """
    import secrets
    from models import WorkerApiKey, UserWorker

    # 1. Check WorkerApiKey in DB
    wak = db.query(WorkerApiKey).filter(WorkerApiKey.api_key == data.api_key.strip()).first()
    user_id = wak.user_id if wak else 1

    # 2. Always generate a NEW dynamic worker_key for this session
    new_dynamic_key = f"WRK-{secrets.token_hex(3).upper()}"

    uw = db.query(UserWorker).filter(UserWorker.user_id == user_id, UserWorker.ativo == True).first()
    if not uw:
        uw = UserWorker(
            user_id=user_id,
            worker_key=new_dynamic_key,
            descricao=data.descricao or f"Worker GUI ({data.hostname or 'Local'})",
            ativo=True
        )
        db.add(uw)
    else:
        uw.worker_key = new_dynamic_key
        if data.descricao:
            uw.descricao = data.descricao

    db.commit()
    db.refresh(uw)

    return {
        "status": "success",
        "worker_key": uw.worker_key,
        "api_key": data.api_key,
        "message": f"Worker registrado com sucesso! Novo código dinâmico: {uw.worker_key}"
    }


