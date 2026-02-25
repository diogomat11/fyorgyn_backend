import sys
import os
# Add Local_worker to path to import Worker modules
sys.path.append(r'c:\dev\Agenda_hub_MultiConv\Local_worker\Worker')

from database import SessionLocal
from models import Job
from datetime import datetime

def test_retry():
    print("Testing manual retry logic from dispatcher...")
    db = SessionLocal()
    try:
        j = db.query(Job).filter(Job.id == 1).first()
        if j:
            print(f"DEBUG JOB 1: id={j.id}, status={repr(j.status)}, attempts={repr(j.attempts)}, locked_by={repr(j.locked_by)}")
            print(f"  Filter status=='error': {j.status == 'error'}")
            print(f"  Filter attempts < 3: {j.attempts < 3}")
            print(f"  Filter locked_by == None: {j.locked_by is None}")
            print(f"  Filter locked_by == '': {j.locked_by == ''}")
            
        # Try individual query pieces
        q_status = db.query(Job).filter(Job.status == "error").all()
        q_att = db.query(Job).filter(Job.attempts < 3).all()
        q_lock = db.query(Job).filter((Job.locked_by == None) | (Job.locked_by == "")).all()
        
        print(f"Query Status match count: {len(q_status)}")
        print(f"Query Attempts match count: {len(q_att)}")
        print(f"Query LockedBy match count: {len(q_lock)}")
        
        print(f"Found {len(failed_jobs)} jobs to retry.")
        for job in failed_jobs:
            print(f"Assigning pending to Job {job.id} (Current: {job.status}, Attempts: {job.attempts})")
            job.status = "pending"
            job.updated_at = datetime.utcnow()
        
        print("Committing...")
        db.commit()
        print("Commit SUCCESS.")
        
    except Exception as e:
        print(f"COMMIT FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_retry()
