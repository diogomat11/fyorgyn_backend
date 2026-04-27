import sys
import os
from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("SELECT job_id, level, message, created_at FROM logs ORDER BY id_log DESC LIMIT 20")).fetchall()
    for row in res:
        print(f"[{row[3]}] Job {row[0]} | {row[1]} | {row[2]}")
