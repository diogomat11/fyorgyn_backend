"""
test_crm_import_flow.py — Testa o pipeline de importação síncrona do Hub salvando direto no banco de dados (corpo_clinico).
"""

import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(ROOT / "backend" / ".env")

import psycopg2

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("ERRO: DATABASE_URL não configurada.")
    sys.exit(1)

conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

resultado_file = ROOT / "backend_worker" / "cfm_resultado_GO.json"
if not resultado_file.exists():
    print(f"ERRO: Arquivo {resultado_file} não encontrado.")
    sys.exit(1)

data = json.loads(resultado_file.read_text(encoding="utf-8"))
medicos = data.get("medicos", [])
print(f"Carregados {len(medicos)} médicos do teste do scraper...")

print("\nPersistindo médicos no banco de dados (tabela public.corpo_clinico)...")
inseridos = 0
atualizados = 0

for m in medicos:
    nome = m["nome"].strip().upper()
    crm = str(m["crm"]).strip()
    uf = m["uf"] or "GO"
    situacao = (m["situacao"] or "ativo").lower()
    especialidades = m.get("especialidades") or []
    area = especialidades[0].upper() if especialidades else "MEDICINA"

    cur.execute("""
        SELECT id FROM public.corpo_clinico
        WHERE conselho ILIKE 'CRM' AND registro = %s AND ("UF" ILIKE %s OR "UF" IS NULL)
        LIMIT 1
    """, (crm, uf))
    row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE public.corpo_clinico
            SET nome = %s, situacao = %s, atualizado_crm = NOW(), "UF" = %s
            WHERE id = %s
        """, (nome, situacao, uf, row[0]))
        atualizados += 1
    else:
        cur.execute("""
            INSERT INTO public.corpo_clinico (
                user_id, id_profissional, nome, conselho, registro, "UF", area,
                status, tipo_profissional, situacao, atualizado_crm
            ) VALUES (
                1, NULL, %s, 'CRM', %s, %s, %s,
                'ativo', 'medico', %s, NOW()
            )
        """, (nome, crm, uf, area, situacao))
        inseridos += 1

print(f"Concluido! Inseridos: {inseridos} novos medicos | Atualizados: {atualizados}")


cur.execute("""
    SELECT id, nome, registro, "UF", conselho, situacao, tipo_profissional, atualizado_crm
    FROM public.corpo_clinico
    WHERE tipo_profissional = 'medico' AND "UF" = 'GO'
    ORDER BY id DESC
    LIMIT 10;
""")
rows = cur.fetchall()

print(f"\nExibindo os 10 últimos médicos inseridos na tabela corpo_clinico:")
for r in rows:
    print(f"  ID {r[0]} | {r[1]} | CRM {r[2]}/{r[3]} ({r[4]}) | Situação: {r[5]} | Atualizado em: {r[7]}")

cur.close()
conn.close()
