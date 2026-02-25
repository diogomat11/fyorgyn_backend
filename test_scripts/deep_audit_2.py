import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def deep_audit():
    load_dotenv('./.env')
    engine = create_engine(os.getenv('DATABASE_URL'))
    with engine.connect() as con:
        # Job 1
        job = con.execute(text("SELECT * FROM jobs WHERE id = 1")).fetchone()
        print("JOB 1 RAW:", job)
        
        # Priority rules
        rules = con.execute(text("SELECT * FROM priority_rules")).fetchall()
        print("PRIORITY RULES:", rules)
        
        # Convenios
        convs = con.execute(text("SELECT id_convenio, nome FROM convenios")).fetchall()
        print("CONVENIOS:", convs)
        
        # Check if dispatcher is locking it
        if job and job.locked_by:
            print(f"CRITICAL: Job 1 is LOCKED by {job.locked_by}")

if __name__ == "__main__":
    deep_audit()
