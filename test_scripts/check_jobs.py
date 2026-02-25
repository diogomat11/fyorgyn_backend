import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from database import SessionLocal
from models import Job

db = SessionLocal()
jobs = db.query(Job).order_by(Job.id.desc()).limit(10).all()

for j in jobs:
    print(f"[{j.id}] Conv:{j.id_convenio} Rotina:{j.rotina} Status:{j.status} Params:{j.params}")
