import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.environ.get("DATABASE_URL")

try:
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cursor = conn.cursor()
    with open("migrations/0046_create_faturamento_lotes.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    cursor.execute(sql)
    print("Migration 0046 executada com sucesso!")
except Exception as e:
    print(f"Erro: {e}")
