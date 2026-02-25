import os
import sys

# Script wrapper to execute the SQL 0034 migration
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from database import engine
from sqlalchemy import text

def apply_migrations():
    print("Aplicando Migration 0034 (Remocao de Auto-Vinculo)...")
    file_34 = os.path.abspath(os.path.join(os.path.dirname(__file__), 'migrations', '0034_drop_trigger_auto_guia.sql'))
    with open(file_34, 'r', encoding='utf-8') as f:
        sql = f.read()
        
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(sql))
            print("Migration 0034 executada com sucesso! A insercao de lotes via CSV nao estourara mais saldos de guias automaticamente.")

if __name__ == "__main__":
    apply_migrations()
