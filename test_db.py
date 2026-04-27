from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text('SELECT id_lote, "detalheId" FROM faturamento_lotes LIMIT 5')).fetchall()
    print("Faturamento Lotes sample:", res)
