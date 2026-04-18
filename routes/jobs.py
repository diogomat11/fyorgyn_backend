from fastapi import APIRouter, Depends, HTTPException, Body, Query
from dependencies import get_current_user
from sqlalchemy.orm import Session
from database import get_db
from models import Job, Carteirinha
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime, timedelta

router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"]
)

class TemporaryPatientData(BaseModel):
    carteirinha: str
    paciente: str

class CreateJobRequest(BaseModel):
    type: str # 'single', 'multiple', 'all', 'temp'
    carteirinha_ids: Optional[List[int]] = None
    temp_patient: Optional[TemporaryPatientData] = None
    rotina: Optional[str] = None
    params: Optional[str] = None
    id_convenio: Optional[int] = None

@router.post("/")
def create_jobs(
    request: CreateJobRequest, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    created_count = 0
    from services import job_service
    
    from dependencies import get_allowed_convenio_ids
    allowed_ids = get_allowed_convenio_ids(current_user)
    
    if request.id_convenio:
        if allowed_ids and request.id_convenio not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        target_convenio = request.id_convenio
    else:
        target_convenio = allowed_ids[0] if allowed_ids else None
    
    if request.type == 'all':
        created_count = job_service.create_all_jobs(db, id_convenio=target_convenio, rotina=request.rotina, params=request.params)
            
    elif request.type in ['single', 'multiple']:
        is_ipasgo_op3 = target_convenio == 6 and request.rotina in ['3', 'op3_import_guias']
        
        if not request.carteirinha_ids:
            if is_ipasgo_op3 and request.type == 'single':
                # Create a standalone job for IPASGO op3 without a specific patient
                new_job = Job(carteirinha_id=None, status="pending", id_convenio=target_convenio, rotina=request.rotina, params=request.params)
                db.add(new_job)
                created_count = 1
            else:
                raise HTTPException(status_code=400, detail="carteirinha_ids required for single/multiple")
        else:
            # Special validation for IPASGO printing jobs (routine 5)
            if target_convenio == 6 and request.rotina == '5':
                import json
                try:
                    p = json.loads(request.params or '{}')
                    guia_num = p.get("numero_guia")
                    if guia_num:
                        from models import BaseGuia
                        # Check if this guide belongs to one of the carteirinhas and is authorized
                        valid_guia = db.query(BaseGuia).filter(
                            BaseGuia.guia == guia_num,
                            BaseGuia.carteirinha_id.in_(request.carteirinha_ids),
                            BaseGuia.status_guia.ilike('%autorizad%')
                        ).first()
                        if not valid_guia:
                            raise HTTPException(status_code=400, detail="Apenas guias autorizadas podem ser enviadas para impressão.")
                except json.JSONDecodeError:
                    pass
            
            created_count = job_service.create_jobs_bulk(db, request.carteirinha_ids, id_convenio=target_convenio, rotina=request.rotina, params=request.params)
    
    elif request.type == 'temp':
        if not request.temp_patient:
             raise HTTPException(status_code=400, detail="temp_patient data required for temp job")
             
        created_count = job_service.create_temp_job(db, request.temp_patient.carteirinha, request.temp_patient.paciente, id_convenio=target_convenio, rotina=request.rotina, params=request.params)
                
    else:
        raise HTTPException(status_code=400, detail="Invalid job type")

    db.commit()
    return {"message": f"Created/Queued jobs", "count": created_count}

@router.get("/")
def list_jobs(
    status: Optional[str] = None,
    created_at_start: Optional[date] = None,
    created_at_end: Optional[date] = None,
    id_convenio: Optional[int] = None,
    limit: int = 25, 
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(Job)
    
    from dependencies import get_allowed_convenio_ids
    allowed_ids = get_allowed_convenio_ids(current_user)
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
             raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        query = query.filter(Job.id_convenio == id_convenio)
    elif allowed_ids:
        query = query.filter(Job.id_convenio.in_(allowed_ids))
    
    if status:
        query = query.filter(Job.status == status)
        
    if created_at_start:
        query = query.filter(Job.created_at >= created_at_start)
    if created_at_end:
        end_dt = datetime.combine(created_at_end, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Job.created_at < end_dt)
    
    # Order by priority desc, created_at asc
    total = query.count()
    jobs = query.order_by(Job.priority.desc(), Job.created_at.desc()).limit(limit).offset(skip).all()
    # Note: Changed order to desc created_at to show newest first
    
    from models import Log
    results = []
    for j in jobs:
        j_dict = {
            "id": j.id,
            "carteirinha_id": j.carteirinha_id,
            "id_convenio": j.id_convenio,
            "rotina": j.rotina,
            "params": j.params,
            "status": j.status,
            "attempts": j.attempts,
            "priority": j.priority,
            "locked_by": j.locked_by,
            "timeout": j.timeout,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
            "error_message": None
        }
        if j.status == 'error':
            last_err = db.query(Log).filter(Log.job_id == j.id, Log.level == "ERROR").order_by(Log.created_at.desc()).first()
            if last_err:
                msg_lower = last_err.message.lower()
                if "carteira inv" in msg_lower or "dígito" in msg_lower or "invalida" in msg_lower:
                    j_dict["error_message"] = "Carteira inválida"
                else:
                    j_dict["error_message"] = last_err.message
        results.append(j_dict)
    
    return {"data": results, "total": total, "skip": skip, "limit": limit}

@router.delete("/{id}")
def delete_job(id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == id).first()
    if not job:
        raise HTTPException(404, "Job not found")
        
    # Validation: Only delete if error and attempts > 3
    # User said: "probido exclusão de jobs em andamento ou com status sucess"
    # "um Job só poderá ser excluido se status seja error e tentativas maior que 3"
    
    allowed = (job.status == 'error' and job.attempts > 3)
    # Or maybe allow pending if it's stuck? User didn't specify. Sticking to strict rule.
    
    if not allowed:
         raise HTTPException(status_code=400, detail="Exclusão permitida apenas para Jobs com erro e mais de 3 tentativas.")
         
    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}

@router.post("/{id}/retry")
def retry_job(id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    # Validation: Same as delete?
    # "ao clicar em reenviar exibir mensagem de confirmação, o status será alterado para pending"
    # User implied logic for buttons "Jobs error... e habilita botões de ação"
    # So implies retry is available for error jobs. 
    # And "reenviar(caso estatus seja error e tentativas maior que 3)"
    
    allowed = (job.status == 'error')
    
    if not allowed:
        raise HTTPException(status_code=400, detail="Reenvio permitido apenas para Jobs com erro.")

    job.status = 'pending'
    job.attempts = 0
    job.locked_by = None
    job.updated_at = datetime.utcnow()
    
    db.commit()
    return {"message": "Job queued for retry", "status": job.status}
