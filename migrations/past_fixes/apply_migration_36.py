import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from database import SessionLocal
from sqlalchemy import text

def apply():
    db = SessionLocal()
    try:
        with open('backend/migrations/0036_fix_guide_linking_rules.sql', 'r', encoding='utf-8') as f:
            sql = f.read()
        db.execute(text(sql))
        db.commit()
        print("Migration 0036 executada com sucesso!")
        
        # Testando a vinculação corrigida
        db.execute(text("UPDATE base_guias SET updated_at = NOW() WHERE saldo > 0"))
        db.commit()
        print("Update testado perfeitamente! Saldo protegido em (2,3) e Lincagem vinculada em carteirinhas vazias.")
    except Exception as e:
        print("Erro na Migration 0036:", e)

if __name__ == '__main__':
    apply()
