from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import Job, Carteirinha, BaseGuia
from sqlalchemy import func, case

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)

from dependencies import get_current_user

from typing import Optional

@router.get("/stats")
def get_dashboard_stats(
    id_convenio: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Isolation
    cart_query = db.query(Carteirinha)
    guia_query = db.query(BaseGuia)
    job_query = db.query(
        func.count(Job.id).label("total"),
        func.sum(case((Job.status == 'success', 1), else_=0)).label("success"),
        func.sum(case((Job.status == 'error', 1), else_=0)).label("error"),
        func.sum(case((Job.status.in_(['pending', 'processing']), 1), else_=0)).label("pending")
    )
    
    from dependencies import get_allowed_convenio_ids
    from fastapi import HTTPException
    
    allowed_ids = get_allowed_convenio_ids(current_user)
    
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
             raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        cart_query = cart_query.filter(Carteirinha.id_convenio == id_convenio)
        guia_query = guia_query.filter(BaseGuia.id_convenio == id_convenio)
        job_query = job_query.filter(Job.id_convenio == id_convenio)
    elif allowed_ids:
        cart_query = cart_query.filter(Carteirinha.id_convenio.in_(allowed_ids))
        guia_query = guia_query.filter(BaseGuia.id_convenio.in_(allowed_ids))
        job_query = job_query.filter(Job.id_convenio.in_(allowed_ids))
    
    total_carteirinhas = cart_query.count()
    total_guias = guia_query.count()
    job_stats = job_query.first()

    total_jobs = job_stats.total or 0
    jobs_success = job_stats.success or 0
    jobs_error = job_stats.error or 0
    jobs_pending = job_stats.pending or 0
    
    return {
        "overview": {
            "total_carteirinhas": total_carteirinhas,
            "total_guias": total_guias,
            "total_jobs": total_jobs
        },
        "jobs_status": {
            "success": jobs_success,
            "error": jobs_error,
            "pending": jobs_pending
        }
    }
