import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for ef in ['.env', '../Local_worker/.env']:
    if os.path.exists(ef):
        with open(ef, encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from database import SessionLocal
from models import User
db = SessionLocal()
for uid in [11, 12, 13]:
    u = db.query(User).filter(User.id == uid).first()
    if u:
        db.delete(u)
        print(f'Deleted user id={uid} ({u.username})')
db.commit()
db.close()
print('Done.')
