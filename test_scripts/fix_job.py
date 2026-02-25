from database import engine
from sqlalchemy import text

def fix_and_audit():
    with engine.connect() as con:
        # Reset Job 1
        con.execute(text("UPDATE jobs SET status = 'pending', attempts = 0, locked_by = NULL WHERE id = 1"))
        con.commit()
        print("Job 1 reset to pending and committed.")
        
        # Audit Rules
        rules = con.execute(text("SELECT * FROM priority_rules")).fetchall()
        print("PRIORITY RULES:", rules)
        
        # Audit Job again
        job = con.execute(text("SELECT * FROM jobs WHERE id = 1")).fetchall()
        print("JOB 1 STATE:", job)

if __name__ == "__main__":
    fix_and_audit()
