"""
run_crm_migrations.py — Aplica as migrations 0083 e 0084 no PostgreSQL de forma idempotente.
"""
import os
import sys
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / "backend" / ".env")

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("ERRO: DATABASE_URL nao encontrada no backend/.env")
    sys.exit(1)

print("Conectando ao banco de dados...")
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

# Garantir colunas e PK de forma idempotente
commands = [
    "ALTER TABLE public.corpo_clinico ADD COLUMN IF NOT EXISTS id SERIAL;",
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corpo_clinico_pkey' AND conrelid = 'public.corpo_clinico'::regclass AND array_length(conkey, 1) > 1) THEN
            ALTER TABLE public.corpo_clinico DROP CONSTRAINT corpo_clinico_pkey;
            ALTER TABLE public.corpo_clinico ADD CONSTRAINT corpo_clinico_pkey PRIMARY KEY (id);
        END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corpo_clinico_id_prof_area_key') THEN
            ALTER TABLE public.corpo_clinico ADD CONSTRAINT corpo_clinico_id_prof_area_key UNIQUE (id_profissional, area);
        END IF;
    END $$;
    """,
    "ALTER TABLE public.corpo_clinico ADD COLUMN IF NOT EXISTS situacao TEXT;",
    "ALTER TABLE public.corpo_clinico ADD COLUMN IF NOT EXISTS atualizado_crm TIMESTAMPTZ;",
    "ALTER TABLE public.corpo_clinico ALTER COLUMN id_profissional DROP NOT NULL;",
]

for cmd in commands:
    try:
        cur.execute(cmd)
    except Exception as e:
        print(f"Aviso no comando: {e}")

print("OK! Migrations de corpo_clinico verificadas/aplicadas.")

print("\nVerificando colunas da tabela public.corpo_clinico:")
cur.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'corpo_clinico' AND table_schema = 'public'
    ORDER BY ordinal_position;
""")
cols = cur.fetchall()
for col in cols:
    print(f"  - {col[0]}: {col[1]} (nullable: {col[2]})")

cur.close()
conn.close()
