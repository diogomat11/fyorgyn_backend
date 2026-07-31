"""
verify_db.py — Verifica os médicos inseridos na tabela public.corpo_clinico
"""
import os
import psycopg2
from dotenv import load_dotenv

from pathlib import Path
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM public.corpo_clinico WHERE tipo_profissional = 'medico' AND \"UF\" = 'GO'")
total = cur.fetchone()[0]
print(f"Total de médicos de GO na tabela corpo_clinico: {total}")

cur.execute("""
    SELECT id, nome, registro, "UF", conselho, situacao, atualizado_crm
    FROM public.corpo_clinico
    WHERE tipo_profissional = 'medico' AND "UF" = 'GO'
    ORDER BY id DESC
    LIMIT 10
""")
rows = cur.fetchall()

print("\nÚltimos 10 médicos gravados no banco:")
for r in rows:
    print(f"  ID {r[0]} | {r[1]} | CRM {r[2]}/{r[3]} ({r[4]}) | Situação: {r[5]} | Atualizado: {r[6]}")

cur.close()
conn.close()
