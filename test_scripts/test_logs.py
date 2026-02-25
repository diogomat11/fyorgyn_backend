import sys, os
sys.path.append(os.path.abspath('backend'))
from database import SessionLocal
from models import BaseGuia

db = SessionLocal()
guias = db.query(BaseGuia).order_by(BaseGuia.id.desc()).limit(15).all()
for g in guias:
    print(f"ID {g.id} | Guia {g.guia} | Convenio {g.id_convenio} | Status {g.status_guia} | Data {g.data_autorizacao}")
