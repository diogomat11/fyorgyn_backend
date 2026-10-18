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

sql_file = ROOT / "migrations" / "0107_add_abrev_areas_atuacao.sql"
with open(sql_file, "r", encoding="utf-8") as f:
    sql = f.read()

print("Executando migration 0107...")
cur.execute(sql)
print("Migration 0107 aplicada com sucesso!")

# Verificar resultado
cur.execute("SELECT id_area, nome, abrev FROM areas_atuacao ORDER BY id_area;")
rows = cur.fetchall()
print("\nResultado:")
print(f"{'id_area':<10} {'nome':<25} {'abrev':<10}")
print("-" * 45)
for r in rows:
    print(f"{r[0]:<10} {r[1]:<25} {r[2] or '':<10}")

cur.close()
conn.close()
