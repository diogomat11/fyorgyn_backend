import os
import sys

# Script wrapper to execute the pure SQL 0031 migration
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from database import engine
from sqlalchemy import text

def apply_31():
    print("Iniciando Migration 0031...")
    filepath = os.path.abspath(os.path.join(os.path.dirname(__file__), 'migrations', '0031_drop_autorizacao_procedimento.sql'))
    with open(filepath, 'r', encoding='utf-8') as f:
        sql = f.read()
    
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(sql))
            print("Migration 0031 executada com sucesso! Coluna autorizacao apagada, e Triggers de Procedimento -> Faturamento religadas.")

if __name__ == "__main__":
    apply_31()
