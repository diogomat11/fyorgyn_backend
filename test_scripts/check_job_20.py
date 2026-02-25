from database import SessionLocal
from models import Log
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

db = SessionLocal()
logs = db.query(Log).filter(Log.job_id == 20).order_by(Log.id.asc()).all()

with open("job_20_logs.txt", "w") as f:
    for log in logs:
        f.write(f"[{log.level}] {log.message}\n")
