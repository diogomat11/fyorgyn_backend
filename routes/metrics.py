from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import JobExecution
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

router = APIRouter()

class ExecutionResponse(BaseModel):
    id: int
    job_id: Optional[int]
    id_convenio: Optional[int]
    rotina: Optional[str]
    status: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    duration_seconds: Optional[int]
    items_found: int
    error_category: Optional[str]
    error_message: Optional[str]
    
    class Config:
        from_attributes = True

@router.get("/", response_model=List[ExecutionResponse])
def list_executions(
    limit: int = 50, 
    id_convenio: Optional[int] = None, 
    db: Session = Depends(get_db)
):
    query = db.query(JobExecution)
    if id_convenio:
        query = query.filter(JobExecution.id_convenio == id_convenio)
    return query.order_by(JobExecution.start_time.desc()).limit(limit).all()

@router.get("/summary")
def get_execution_summary(db: Session = Depends(get_db)):
    # Simple summary of success vs error
    total = db.query(JobExecution).count()
    success = db.query(JobExecution).filter(JobExecution.status == "success").count()
    errors = db.query(JobExecution).filter(JobExecution.status == "error").count()
    
    return {
        "total": total,
        "success": success,
        "errors": errors,
        "success_rate": (success / total * 100) if total > 0 else 0
    }
