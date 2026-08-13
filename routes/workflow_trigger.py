from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Agendamento, User, UserConvenio, UserConvenioWorkflow, UnidadePrestador, Job
from dependencies import get_current_user, get_current_worker_key
from services.workflow_engine import WorkflowEngine
from pydantic import BaseModel

router = APIRouter(
    prefix="/workflow",
    tags=["Workflow Trigger"]
)

class TriggerWorkflowRequest(BaseModel):
    agendamento_ids: List[int]

@router.post("/trigger")
def trigger_agendamento_workflow(
    req: TriggerWorkflowRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    worker_key: Optional[str] = Depends(get_current_worker_key)
):
    if not req.agendamento_ids:
        raise HTTPException(status_code=400, detail="Nenhum agendamento selecionado.")

    query = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids))
    if not current_user.is_admin:
        query = query.filter(Agendamento.user_id == current_user.id)

    agendamentos = query.all()
    if not agendamentos:
        raise HTTPException(status_code=404, detail="Nenhum agendamento encontrado.")

    first_ag = agendamentos[0]
    id_convenio = first_ag.id_convenio
    parent_id = current_user.parent_user_id if current_user.parent_user_id else current_user.id

    # Resolve cod_prestador
    cod_prestador = first_ag.cod_prestador
    if not cod_prestador:
        # Resolve via UnidadePrestador / UserConvenio
        uc = db.query(UserConvenio).filter(
            UserConvenio.user_id == parent_id,
            UserConvenio.id_convenio == id_convenio
        ).first()
        cod_prestador = uc.cod_prestador if uc else "2209525"

    # Check for custom UserConvenioWorkflow
    wf = db.query(UserConvenioWorkflow).filter(
        UserConvenioWorkflow.user_id == parent_id,
        UserConvenioWorkflow.id_convenio == id_convenio
    ).first()

    if wf and wf.fluxo_passos:
        # Create chain of jobs using WorkflowEngine
        created_jobs = WorkflowEngine.create_workflow_chain(
            db=db,
            user_id=current_user.id,
            id_convenio=id_convenio,
            agendamento_ids=req.agendamento_ids,
            fluxo_passos=wf.fluxo_passos,
            worker_key=worker_key,
            cod_prestador=cod_prestador
        )
        return {
            "status": "success",
            "message": f"Workflow '{wf.nome_workflow}' acionado. {len(created_jobs)} jobs encadeados.",
            "jobs": [{"id": j.id, "rotina": j.rotina, "status": j.status} for j in created_jobs]
        }

    # Fallback default workflow (e.g. op5_confirmar)
    rotina = "op5_confirmar" if id_convenio == 101 else "op2_executar"
    job_params = {
        "agendamento_ids": req.agendamento_ids,
        "cod_prestador": cod_prestador,
        "id_convenio": id_convenio
    }
    default_job = Job(
        user_id=current_user.id,
        id_convenio=id_convenio,
        rotina=rotina,
        params=job_params,
        status="pending",
        worker_key=worker_key
    )
    db.add(default_job)
    db.commit()
    db.refresh(default_job)

    # Update status on agendamentos
    db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids)).update({
        Agendamento.execucao_status: "processando"
    }, synchronize_session=False)
    db.commit()

    return {
        "status": "success",
        "message": f"Job de {rotina} enfileirado com sucesso.",
        "jobs": [{"id": default_job.id, "rotina": default_job.rotina, "status": default_job.status}]
    }
