import os
import sys

# Script wrapper to execute the SQL 0031 e 0033 migration
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from database import engine
from sqlalchemy import text

def apply_triggers():
    print("Re-aplicando Migration 0031...")
    file_31 = os.path.abspath(os.path.join(os.path.dirname(__file__), 'migrations', '0031_agendamentos_triggers.sql'))
    with open(file_31, 'r', encoding='utf-8') as f:
        sql_31 = f.read()
        
    print("Aplicando Migration 0033...")
    file_33 = os.path.abspath(os.path.join(os.path.dirname(__file__), 'migrations', '0033_agendamentos_busca_guia.sql'))
    with open(file_33, 'r', encoding='utf-8') as f:
        sql_33 = f.read()
    
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(sql_31))
            conn.execute(text(sql_33))
            print("Migrations executadas com sucesso! Agendamentos ativamente procuram Guias agora.")

if __name__ == "__main__":
    apply_triggers()
