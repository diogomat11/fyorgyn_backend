import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import engine
from sqlalchemy import text

if __name__ == '__main__':
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sql_file = os.path.join(current_dir, '0101_create_relatorios_medicos.sql')

    with open(sql_file, 'r', encoding='utf-8-sig') as f:
        sql_commands = f.read()

    with engine.begin() as conn:
        conn.execute(text(sql_commands))

    print('Migration 0101 executed successfully.')
