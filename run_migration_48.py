import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.environ.get("DATABASE_URL")

try:
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cursor = conn.cursor()
    with open("migrations/0048_add_op12_ipasgo.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    cursor.execute(sql)
    print("Migration 0048 (OP12 IPASGO) executada com sucesso!")
except Exception as e:
    print(f"Erro: {e}")
