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

@router.get("/")
def list_workers(db: Session = Depends(get_db)):
    """
    List all registered workers.
    """
    return worker_service.get_all_workers(db)

@router.post("/{worker_id}/restart")
def restart_worker(worker_id: int, db: Session = Depends(get_db)):
    """
    Queue a restart command for a worker.
    """
    worker = worker_service.queue_restart_command(db, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return {"message": "Restart command queued", "worker": worker.hostname}
