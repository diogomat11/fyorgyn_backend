import os
import sys
import time
import requests
import psycopg2
from dotenv import load_dotenv

_backend_dir = os.path.dirname(os.path.abspath(__file__)) if os.path.basename(os.getcwd()) != 'backend' else os.getcwd()
load_dotenv(os.path.join(_backend_dir, '.env'))

DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
DB_PASSWORD = os.getenv("SUPABASE_PASSWORD", "")
DB_HOST = os.getenv("SUPABASE_DB_HOST", "")
DB_PORT = os.getenv("SUPABASE_DB_PORT", "5432")
DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    print("Disabling old jobs...")
    cur.execute("UPDATE jobs SET status='error', locked_by=NULL WHERE id IN (718, 719)")
    conn.commit()

    print("Triggering new Capture Job...")
    try:
        r = requests.post('http://localhost:8000/agendamentos/capturar', json={'agendamento_id': 837})
        print('POST Response:', r.json())
        new_job_id = r.json().get('job_id')
    except Exception as e:
        print("Failed to post:", e)
        new_job_id = None

    if not new_job_id:
        cur.close()
        conn.close()
        return

    print("Waiting 30 seconds for execution...")
    time.sleep(30)
    
    cur.execute("SELECT id, rotina, status, attempts FROM jobs WHERE id = %s", (new_job_id,))
    print('NEW JOB:', cur.fetchone())
    
    cur.execute("SELECT status, error_message, duration_seconds FROM job_executions WHERE job_id = %s ORDER BY id DESC LIMIT 1", (new_job_id,))
    print('EXEC:', cur.fetchone())

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
