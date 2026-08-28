from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from services.cron_service import list_crons, update_cron_config, run_cron_by_id

router = APIRouter(prefix="/crons", tags=["crons"])


class CronConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    horario: Optional[str] = None  # formato HH:MM
    rotina: Optional[str] = None


def _require_admin(current_user):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")


@router.get("")
@router.get("/")
def get_crons(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Lista configurações de cron por integrador (painel Agendamentos)."""
    _require_admin(current_user)
    return list_crons(db)


@router.put("/{cron_id}")
def update_cron(cron_id: int, req: CronConfigUpdateRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Atualiza habilitação, horário ou rotina de um cron."""
    _require_admin(current_user)
    cron = update_cron_config(db, cron_id, enabled=req.enabled, horario=req.horario, rotina=req.rotina)
    return {
        "status": "success",
        "id": cron.id,
        "enabled": bool(cron.enabled),
        "horario": cron.horario.strftime("%H:%M") if cron.horario else None,
        "rotina": cron.rotina,
    }


@router.post("/{cron_id}/run-now")
def run_cron_now(cron_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Dispara o cron imediatamente (ignora horário/agenda; atualiza ultimo_run)."""
    _require_admin(current_user)
    resumo = run_cron_by_id(db, cron_id, force=True)
    return {"status": "success", **resumo}
