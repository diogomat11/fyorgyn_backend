from sqlalchemy.orm import Session
from models import Worker
from datetime import datetime, timezone, timedelta
import json

def register_heartbeat(db: Session, hostname: str, status: str, current_job_id: int = None, meta: dict = None):
    """
    Registers a heartbeat from a worker.
    Creates the worker if it doesn't exist.
    Updates status, last_heartbeat, and other fields.
    Returns any pending command for the worker.
    """
    worker = db.query(Worker).filter(Worker.hostname == hostname).first()
    
    now = datetime.now(timezone.utc)
    
    if not worker:
        worker = Worker(hostname=hostname)
        db.add(worker)
    
    worker.status = status
    worker.last_heartbeat = now
    worker.current_job_id = current_job_id
    if meta:
        worker.meta = json.dumps(meta)

    # Auto-Restart Logic for stuck Error state
    if status == 'error':
        if not worker.first_error_at:
            worker.first_error_at = now
        else:
            # Check duration
            diff = now - worker.first_error_at
            if diff > timedelta(minutes=15):
                worker.command = "restart"
                worker.first_error_at = None # Reset timer after queuing restart
    else:
        # If status is NOT error (idle, processing), clear the error timer
        if worker.first_error_at:
            worker.first_error_at = None
    
    # Check for pending command
    command = worker.command
    
    # If command is 'restart', we send it once and then clear it? 
    # Or strict 'ack' logic? For simplicity, we assume if we send it, it's received.
    # But usually a worker would acknowledge. 
    # Let's keep it simple: if command is present, we return it. 
    # The worker should probably clear it via a separate call or we clear it here?
    # Clearing it here implies "at most once" delivery (if net fails, command lost).
    # Not clearing implies "at least once" (worker might restart loop).
    # Let's clear it here for now as a simple signal.
    
    if worker.command:
        worker.command = None 
        
    db.commit()
    db.refresh(worker)
    
    return {"command": command}

def get_all_workers(db: Session):
    """
    Returns all workers.
    Logic to mark them as 'offline' if heartbeat > 1 min is NOT persisted here to avoid DB writes on GET.
    Frontend or specific cleanup job should handle that visual status.
    """
    return db.query(Worker).all()

def queue_restart_command(db: Session, worker_id: int):
    """
    Queues a restart command for a specific worker.
    """
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if worker:
        worker.command = "restart"
        db.commit()
        db.refresh(worker)
    return worker
