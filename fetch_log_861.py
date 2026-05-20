import os
import sys
from database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    res = db.execute(text("SELECT message FROM logs WHERE job_id=861 AND message LIKE '%Raw API Response%' ORDER BY id DESC LIMIT 5")).fetchall()
    for row in res:
        print("LOG:", row[0])
    
    # Also just get the last 5 logs for job 861 if no Raw API matches
    if not res:
        res2 = db.execute(text("SELECT message FROM logs WHERE job_id=861 ORDER BY id DESC LIMIT 10")).fetchall()
        for row in res2:
            print("LOG2:", row[0])
finally:
    db.close()
