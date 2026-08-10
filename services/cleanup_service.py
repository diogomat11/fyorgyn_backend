
from sqlalchemy.orm import Session
from models import Carteirinha, Job
from datetime import datetime, timezone, timedelta
import logging
import os
import json
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def delete_expired_patients(db: Session):
    """
    Deletes temporary patients whose expiration time has passed.
    Cascading deletes should handle related Jobs, Guias, and PEI records.
    """
    try:
        now = datetime.now(timezone.utc)
        
        # In SQLalchemy, DateTime with timezone=True usually works with timezone-aware datetimes.
        # Ensure DB is storing with TZ or consistently. Postgre constraints 'TIMESTAMP WITH TIME ZONE'
        
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

def cleanup_expired_attachments(db: Session):
    """
    Remove arquivos de anexos locais de jobs concluídos há mais de 24 horas.
    Também remove arquivos órfãos (arquivos na pasta uploads/anexos com mais de 48 horas).
    """
    logger.info("Cleanup: Iniciando limpeza de anexos expirados...")
    
    # 1. Limpar arquivos locais associados a jobs concluídos há mais de 24 horas
    try:
        # Pega jobs finalizados (success ou error) atualizados há mais de 24 horas
        cutoff = datetime.utcnow() - timedelta(hours=24)
        completed_jobs = db.query(Job).filter(
            Job.status.in_(["success", "error"]),
            Job.updated_at < cutoff
        ).all()
        
        removed_count = 0
        for job in completed_jobs:
            if not job.params:
                continue
            
            # Decodifica params do job
            try:
                params = json.loads(job.params) if isinstance(job.params, str) else job.params
            except Exception:
                continue
                
            if not params or not isinstance(params, dict):
                continue
                
            # Identifica caminhos locais nos campos de anexos
            for field in ["anexo_RM", "anexo_AI", "anexo_RC"]:
                val = params.get(field)
                if val and isinstance(val, str) and val.startswith("/uploads/anexos/"):
                    local_path = val.lstrip("/")
                    if os.path.exists(local_path):
                        try:
                            os.remove(local_path)
                            removed_count += 1
                            logger.info(f"Cleanup: Removido anexo expirado do job {job.id}: {local_path}")
                        except Exception as e:
                            logger.error(f"Cleanup: Erro ao remover {local_path}: {e}")
                            
            # Também verifica a lista "anexos" se houver
            anexos_list = params.get("anexos", [])
            if isinstance(anexos_list, list):
                for anx in anexos_list:
                    if not isinstance(anx, dict):
                        continue
                    val = anx.get("caminho")
                    if val and isinstance(val, str) and val.startswith("/uploads/anexos/"):
                        local_path = val.lstrip("/")
                        if os.path.exists(local_path):
                            try:
                                os.remove(local_path)
                                removed_count += 1
                                logger.info(f"Cleanup: Removido anexo da lista expirado do job {job.id}: {local_path}")
                            except Exception as e:
                                logger.error(f"Cleanup: Erro ao remover {local_path}: {e}")
                                
        if removed_count > 0:
            logger.info(f"Cleanup: Total de {removed_count} arquivos de anexos expirados removidos.")
    except Exception as e:
        logger.error(f"Cleanup: Erro ao limpar anexos de jobs finalizados: {e}")
        try: db.rollback()
        except Exception: pass
        
    # 2. Limpar arquivos órfãos com mais de 48 horas (ex: uploads não submetidos)
    try:
        anexos_dir = os.path.join("uploads", "anexos")
        if os.path.exists(anexos_dir):
            now_ts = time.time()
            orphan_cutoff_ts = now_ts - (48 * 3600)  # 48 horas em segundos
            orphan_count = 0
            
            for filename in os.listdir(anexos_dir):
                file_path = os.path.join(anexos_dir, filename)
                if os.path.isfile(file_path):
                    mtime = os.path.getmtime(file_path)
                    if mtime < orphan_cutoff_ts:
                        try:
                            os.remove(file_path)
                            orphan_count += 1
                            logger.info(f"Cleanup: Removido arquivo órfão expirado: {file_path}")
                        except Exception as e:
                            logger.error(f"Cleanup: Erro ao remover arquivo órfão {file_path}: {e}")
                            
            if orphan_count > 0:
                logger.info(f"Cleanup: Total de {orphan_count} arquivos órfãos expirados removidos.")
    except Exception as e:
        logger.error(f"Cleanup: Erro ao limpar arquivos órfãos: {e}")

