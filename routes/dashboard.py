from fastapi import APIRouter, Depends, BackgroundTasks
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
    background_tasks: BackgroundTasks,
    id_convenio: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    cache_params = {
        "id_convenio": id_convenio
    }
    
    # Auto-sincronizar antes do dashboard em background para não travar
    try:
        from services.guias_sync_service import sync_completed_worker_jobs_bg
        background_tasks.add_task(sync_completed_worker_jobs_bg)
    except Exception as e:
        print(f"Error scheduling completed jobs during dashboard load: {e}")
        
    from cache import cache
    cached_res = cache.get(current_user.id, "dashboard", cache_params)
    if cached_res:
        return cached_res

    # Isolation
    cart_query = db.query(Carteirinha)
    guia_query = db.query(BaseGuia)
    job_query = db.query(
        func.count(Job.id).label("total"),
        func.sum(case((Job.status == 'success', 1), else_=0)).label("success"),
        func.sum(case((Job.status == 'error', 1), else_=0)).label("error"),
        func.sum(case((Job.status.in_(['pending', 'processing']), 1), else_=0)).label("pending")
    )
    if not current_user.is_admin:
        cart_query = cart_query.filter(Carteirinha.user_id == current_user.id)
        guia_query = guia_query.filter(BaseGuia.user_id == current_user.id)
        job_query = job_query.filter(Job.user_id == current_user.id)
    
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
    
    # Obter contagens de solicitações por status
    from models import Solicitacao
    sol_query = db.query(Solicitacao)
    if not current_user.is_admin:
        sol_query = sol_query.filter(Solicitacao.user_id == current_user.id)
    
    if id_convenio:
        sol_query = sol_query.filter(Solicitacao.id_convenio == id_convenio)
    elif allowed_ids:
        sol_query = sol_query.filter(Solicitacao.id_convenio.in_(allowed_ids))
        
    sol_status_counts = sol_query.group_by(Solicitacao.status_solicitacao).with_entities(
        Solicitacao.status_solicitacao, func.count(Solicitacao.id)
    ).all()
    
    sol_counts = {"Pendente": 0, "Negada": 0, "Cancelada": 0}
    for status_name, count_val in sol_status_counts:
        status_clean = str(status_name).strip().lower()
        if "autorizad" in status_clean or "liberad" in status_clean:
            continue
        elif "pendente" in status_clean or "estudo" in status_clean or "fila" in status_clean:
            sol_counts["Pendente"] += count_val
        elif "negad" in status_clean:
            sol_counts["Negada"] += count_val
        elif "cancelad" in status_clean:
            sol_counts["Cancelada"] += count_val
        else:
            sol_counts["Pendente"] += count_val
            
    total_autorizadas = total_guias
    total_all = total_autorizadas + sum(sol_counts.values())
    
    res_payload = {
        "overview": {
            "total_carteirinhas": total_carteirinhas,
            "total_guias": total_guias,
            "total_jobs": total_jobs,
            "guias_status": {
                "total": total_all,
                "autorizadas": total_autorizadas,
                "pendentes": sol_counts["Pendente"],
                "negadas": sol_counts["Negada"],
                "canceladas": sol_counts["Cancelada"]
            }
        },
        "jobs_status": {
            "success": jobs_success,
            "error": jobs_error,
            "pending": jobs_pending
        }
    }
    
    cache.set(current_user.id, "dashboard", cache_params, res_payload, ttl=10)
    return res_payload
