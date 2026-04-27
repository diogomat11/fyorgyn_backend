from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Vincula faturamentos órfãos ao lote 1
    res = conn.execute(text('UPDATE faturamento_lotes SET id_lote = 1 WHERE id_lote IS NULL'))
    conn.commit()
    print(f"Linhas atualizadas: {res.rowcount}")
