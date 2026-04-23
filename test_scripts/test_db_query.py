import sys
import os
sys.path.append(os.path.abspath("."))
from database import SessionLocal
from models import Agendamento, BaseGuia
db = SessionLocal()
try:
    from sqlalchemy.orm import aliased
    bg = aliased(BaseGuia)
    agendamentos = (
        db.query(Agendamento, bg.saldo.label("saldo_guia"), bg.timestamp_captura.label("timestamp_captura"))
        .outerjoin(bg, Agendamento.numero_guia == bg.guia)
        .limit(2)
        .all()
    )
    print("Success:", agendamentos)
except Exception as e:
    import traceback
    traceback.print_exc()
