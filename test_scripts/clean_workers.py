import sys, os
sys.path.append(os.path.abspath('backend'))
from database import SessionLocal
from models import Worker

db = SessionLocal()
workers = db.query(Worker).all()
for w in workers:
    print(w.hostname)
    # se terminar em 9005, 9006, 9007, 9008, 9009
    port = int(w.hostname.split('-')[-1])
    if port > 9004:
        print(f"Deleting {w.hostname}")
        db.delete(w)

db.commit()
print("Cleaned!")
