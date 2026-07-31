"""
query_regular.py
"""
import os
import psycopg2
from dotenv import load_dotenv

from pathlib import Path
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

cur.execute("""
    SELECT id, nome, registro, "UF", situacao, atualizado_crm
    FROM public.corpo_clinico
    WHERE situacao = 'regular'
    ORDER BY atualizado_crm DESC
    LIMIT 10
""")
rows = cur.fetchall()

print(f"Médicos gravados com situação 'regular' ({len(rows)} amostra):")
for r in rows:
    print(f"  ID {r[0]} | {r[1]} | CRM {r[2]}/{r[3]} | Situação: {r[4]} | Atualizado: {r[5]}")

cur.close()
conn.close()
