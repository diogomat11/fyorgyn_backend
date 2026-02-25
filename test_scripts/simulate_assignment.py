import sys
import os
from datetime import datetime
# Add Local_worker to path
sys.path.append(r'c:\dev\Agenda_hub_MultiConv\Local_worker\Worker')

from database import SessionLocal
from models import Job

def simulate_assignment():
    print("Simulating Dispatcher Assignment Phase...")
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == 1).first()
        if not job:
            print("Job 1 not found!")
            return
            
        print(f"Initial State: id={job.id}, status={job.status}, attempts={job.attempts}, locked_by={job.locked_by}")
        
        # Dispatcher logic:
        job.status = "processing"
        job.locked_by = "http://127.0.0.1:9000"
        job.attempts += 1
        job.updated_at = datetime.utcnow()
        
        print("Attempting to COMMIT assignment...")
        db.commit()
        print("Commit SUCCESS.")
        
        # Verify
        db.refresh(job)
        print(f"Final State: id={job.id}, status={job.status}, attempts={job.attempts}, locked_by={job.locked_by}")
        
    except Exception as e:
        print(f"ASSIGNMENT COMMIT FAILED: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    simulate_assignment()
