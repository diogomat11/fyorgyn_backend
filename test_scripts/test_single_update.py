
import os
import sys
from sqlalchemy import text

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _backend_dir)

from database import SessionLocal

def test():
    db = SessionLocal()
    try:
        # Single update for ID 263
        sql = """
            UPDATE agendamentos a
            SET cod_procedimento_fat = p.faturamento
            FROM procedimentos p
            WHERE LTRIM(TRIM(a.cod_procedimento_aut), '0') = LTRIM(TRIM(p.codigo_procedimento), '0')
              AND a.id_convenio = p.id_convenio
              AND a.id_agendamento = 263
        """
        result = db.execute(text(sql))
        db.commit()
        print(f"Update for ID 263. Rows affected: {result.rowcount}")
        
        # Verify
        row = db.execute(text("SELECT cod_procedimento_aut, cod_procedimento_fat FROM agendamentos WHERE id_agendamento = 263")).fetchone()
        if row:
            print(f"Result for ID 263: Aut='{row[0]}', Fat='{row[1]}'")
        else:
            print("ID 263 NOT FOUND")
        
    finally:
        db.close()

if __name__ == "__main__":
    test()
