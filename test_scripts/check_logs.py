from database import engine
from sqlalchemy import text

def check_logs():
    with engine.connect() as con:
        logs = con.execute(text("SELECT level, message, created_at FROM logs ORDER BY created_at DESC LIMIT 10")).fetchall()
        print("RECENT LOGS:")
        for l in logs:
            print(f"[{l[0]}] {l[1]} ({l[2]})")
            
        execs = con.execute(text("SELECT id, job_id, status, error_message, start_time FROM job_executions ORDER BY start_time DESC LIMIT 5")).fetchall()
        print("\nRECENT EXECUTIONS:")
        for e in execs:
            print(f"ID={e[0]}, Job={e[1]}, Status={e[2]}, Error={e[3]}, Start={e[4]}")

if __name__ == "__main__":
    check_logs()
