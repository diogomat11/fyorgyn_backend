"""Migration 0037: Job Orchestrator Enhancements"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    sql_path = os.path.join(os.path.dirname(__file__), "migrations", "0037_orchestrator_enhancements.sql")
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()
    
    cur.execute(sql)
    print("Migration 0037 applied successfully.")
    cur.close()
    conn.close()

if __name__ == "__main__":
    run()
