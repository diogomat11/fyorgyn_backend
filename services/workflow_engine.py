from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from models import Job, UserConvenioWorkflow, User, UnidadePrestador, UserConvenio, GuiaLock

class WorkflowEngine:
    @staticmethod
    def create_workflow_chain(
        db: Session,
        user_id: int,
        id_convenio: int,
        agendamento_ids: List[int],
        fluxo_passos: List[Dict[str, Any]],
        worker_key: Optional[str] = None,
        cod_prestador: Optional[str] = None
    ) -> List[Job]:
        """
        Cria uma cadeia de jobs encadeados via depending_id.
        O primeiro job fica com status='pending', os subsequentes ficam com status='waiting_dependency'.
        O dispatcher (worker/backend_worker) liberará os jobs em sequência para o mesmo worker.
        """
        created_jobs = []
        previous_job_id = None

        for idx, passo in enumerate(fluxo_passos):
            rotina = passo.get("rotina") or passo.get("passo")
            if not rotina:
                continue

            params = {
                "agendamento_ids": agendamento_ids,
                "cod_prestador": cod_prestador,
                "workflow_step": idx + 1,
                "total_steps": len(fluxo_passos)
            }

            job_status = "pending" if previous_job_id is None else "waiting_dependency"

            job = Job(
                user_id=user_id,
                id_convenio=id_convenio,
                rotina=rotina,
                params=params,
                status=job_status,
                depending_id=previous_job_id,
                worker_key=worker_key
            )
            db.add(job)
            db.flush()
            previous_job_id = job.id
            created_jobs.append(job)

        db.commit()
        return created_jobs

    @staticmethod
    def resume_workflow(db: Session, failed_job_id: int) -> Optional[Job]:
        """
        Retoma um workflow a partir do job que falhou, recriando o passo falhado 
        e mantendo a dependência dos passos subsequentes.
        """
        failed_job = db.query(Job).filter(Job.id == failed_job_id).first()
        if not failed_job or failed_job.status != "error":
            return None

        # Re-create the failed step without depending_id (since previous step succeeded)
        new_job = Job(
            user_id=failed_job.user_id,
            id_convenio=failed_job.id_convenio,
            rotina=failed_job.rotina,
            params=failed_job.params,
            status="pending",
            depending_id=None,
            worker_key=failed_job.worker_key
        )
        db.add(new_job)
        db.flush()

        # Update dependent jobs to point to the new job
        dependent_jobs = db.query(Job).filter(Job.depending_id == failed_job_id).all()
        for dep in dependent_jobs:
            dep.depending_id = new_job.id

        db.commit()
        return new_job
