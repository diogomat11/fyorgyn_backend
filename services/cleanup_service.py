
from sqlalchemy.orm import Session
from models import Carteirinha, Job
from datetime import datetime, timezone, timedelta
import logging
import json

from services import storage_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def delete_expired_patients(db: Session):
    """
    Deletes temporary patients whose expiration time has passed.
    Cascading deletes should handle related Jobs, Guias, and PEI records.
    """
    try:
        now = datetime.now(timezone.utc)

        expired_patients = db.query(Carteirinha).filter(
            Carteirinha.is_temporary == True,
            Carteirinha.expires_at <= now
        ).all()

        count = len(expired_patients)
        if count > 0:
            logger.info(f"Cleanup: Found {count} expired temporary patients. Deleting...")

            for patient in expired_patients:
                logger.info(f"Deleting expired patient: {patient.carteirinha} (ID: {patient.id})")
                db.delete(patient)

            db.commit()
            logger.info("Cleanup successfully completed.")
        else:
            logger.debug("Cleanup: No expired patients found.")

        return count

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        db.rollback()
        return 0


def _is_anexo_url(val) -> bool:
    """True se val referencia um anexo (signed URL do Storage ou path local legado)."""
    if not isinstance(val, str) or not val:
        return False
    return val.startswith("/uploads/anexos/") or ("supabase" in val and "/anexos/" in val)


def cleanup_expired_attachments(db: Session):
    """
    Remove anexos de jobs concluidos ha mais de 24h (Supabase Storage bucket
    'anexos' ou filesystem local legado) + anexos orfaos com mais de 48h.
    """
    logger.info("Cleanup: Iniciando limpeza de anexos expirados...")

    # 1. Anexos associados a jobs finalizados ha mais de 24 horas
    try:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        completed_jobs = db.query(Job).filter(
            Job.status.in_(["success", "error"]),
            Job.updated_at < cutoff
        ).all()

        removed_count = 0
        for job in completed_jobs:
            if not job.params:
                continue

            try:
                params = json.loads(job.params) if isinstance(job.params, str) else job.params
            except Exception:
                continue

            if not params or not isinstance(params, dict):
                continue

            urls_para_deletar = []
            for field in ("anexo_RM", "anexo_AI", "anexo_RC"):
                if _is_anexo_url(params.get(field)):
                    urls_para_deletar.append(params[field])

            anexos_list = params.get("anexos", [])
            if isinstance(anexos_list, list):
                for anx in anexos_list:
                    if isinstance(anx, dict) and _is_anexo_url(anx.get("caminho")):
                        urls_para_deletar.append(anx["caminho"])

            for url in urls_para_deletar:
                if storage_service.delete_anexo_by_url(url):
                    removed_count += 1
                    logger.info(f"Cleanup: anexo removido do job {job.id}: {url[:80]}")

        if removed_count > 0:
            logger.info(f"Cleanup: {removed_count} anexos de jobs finalizados removidos.")
    except Exception as e:
        logger.error(f"Cleanup: Erro ao limpar anexos de jobs finalizados: {e}")
        try: db.rollback()
        except Exception: pass

    # 2. Anexos orfaos (sem job) com mais de 48 horas
    try:
        orphan_count = storage_service.cleanup_orphan_anexos(max_age_seconds=48 * 3600)
        if orphan_count > 0:
            logger.info(f"Cleanup: {orphan_count} anexos orfaos removidos.")
    except Exception as e:
        logger.error(f"Cleanup: Erro ao limpar anexos orfaos: {e}")
