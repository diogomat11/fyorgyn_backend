import json
from database import SessionLocal
from models import Job

def main():
    db = SessionLocal()
    try:
        jobs = db.query(Job).order_by(Job.id.desc()).limit(20).all()
        out = [{"id": j.id, "convenio": j.id_convenio, "rotina": j.rotina, "status": j.status, "carteirinha_id": j.carteirinha_id, "attempts": j.attempts} for j in jobs]
        with open("test_jobs.json", "w") as f:
            json.dump(out, f, indent=2)
        print("Success")
    finally:
        db.close()

if __name__ == "__main__":
    main()
