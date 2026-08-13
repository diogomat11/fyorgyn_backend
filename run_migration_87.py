import os
import sys
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("ERRO: DATABASE_URL nao encontrada")
    sys.exit(1)

print("Conectando ao banco de dados...")
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

sql_file = ROOT / "migrations" / "0087_user_auth_profiles.sql"
with open(sql_file, "r", encoding="utf-8") as f:
    sql = f.read()

print("Executando migration 0087...")
cur.execute(sql)
print("Migration 0087 aplicada com sucesso!")

cur.close()
conn.close()
