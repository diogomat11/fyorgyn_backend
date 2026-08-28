"""
QA-02 — Validação pós-migrations 0096-0100 (plano de ajustes 2026-08).

Script manual (padrão test_scripts): conecta ao banco via database.SessionLocal
e verifica estrutura + seeds + backfill das migrations do plano.

Uso:
    python test_scripts/validate_migrations_0096_0100.py

Verifica:
  0096 - convenios.prazo_pei_dias / guias_multiprestador + seed Goiania (3)
  0097 - corpo_clinico.id_area + backfill + N:N (areas/convenios/procedimentos/users)
  0098 - cron_configs + seed integrador 3 habilitado
  0099 - views retrocompat worker.convenios/convenio_operacoes removidas
  0100 - trigger func_vincula_guia_a_agendamento com regra v_multiprestador
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import SessionLocal


def check(session, label, sql, expect_fn):
    try:
        rows = session.execute(text(sql)).fetchall()
        ok, detail = expect_fn(rows)
        status = "OK  " if ok else "FAIL"
        print(f"[{status}] {label}: {detail}")
        return ok
    except Exception as e:
        print(f"[FAIL] {label}: erro {e}")
        return False


def main():
    db = SessionLocal()
    falhas = 0

    print("== Migration 0096: convenios (prazo PEI + multiprestador) ==")
    falhas += not check(
        db, "Colunas existem",
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='convenios' AND column_name IN ('prazo_pei_dias','guias_multiprestador')",
        lambda rows: (len(rows) == 2, f"{len(rows)}/2 colunas encontradas")
    )
    falhas += not check(
        db, "Seed Unimed Goiania multiprestador",
        "SELECT guias_multiprestador, prazo_pei_dias FROM convenios WHERE id_convenio=3",
        lambda rows: (bool(rows) and rows[0][0] is True, f"goiania={rows[0] if rows else 'sem registro'}")
    )
    falhas += not check(
        db, "CHECK constraint prazo 30..365",
        "SELECT 1 FROM information_schema.table_constraints WHERE table_name='convenios' AND constraint_type='CHECK'",
        lambda rows: (len(rows) > 0, f"{len(rows)} check constraint(s)")
    )

    print("\n== Migration 0097: corpo_clinico relacoes ==")
    falhas += not check(
        db, "Coluna corpo_clinico.id_area existe",
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='corpo_clinico' AND column_name='id_area'",
        lambda rows: (rows[0][0] == 1, "ok" if rows[0][0] == 1 else "ausente")
    )
    falhas += not check(
        db, "Tabelas N:N existem",
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('corpo_clinico_areas','corpo_clinico_convenios','corpo_clinico_procedimentos','corpo_clinico_users')",
        lambda rows: (len(rows) == 4, f"{len(rows)}/4 tabelas")
    )
    falhas += not check(
        db, "Backfill id_area (areas com match no catalogo)",
        """SELECT COUNT(*) FROM corpo_clinico cc
           WHERE cc.area IS NOT NULL AND cc.area <> '' AND cc.id_area IS NOT NULL""",
        lambda rows: (True, f"{rows[0][0]} linhas com id_area preenchido")
    )
    falhas += not check(
        db, "Nao-correspondencias do backfill (revisar manualmente)",
        """SELECT cc.id, cc.nome, cc.area FROM corpo_clinico cc
           WHERE cc.area IS NOT NULL AND cc.area <> '' AND cc.id_area IS NULL
           ORDER BY cc.area LIMIT 20""",
        lambda rows: (True, f"{len(rows)} linha(s) sem match (lista acima/impressa abaixo)")
    )
    falhas += not check(
        db, "Seed corpo_clinico_areas consistente",
        """SELECT COUNT(*) FROM corpo_clinico cc
           WHERE cc.id_area IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM corpo_clinico_areas a WHERE a.corpo_clinico_id=cc.id AND a.id_area=cc.id_area)""",
        lambda rows: (rows[0][0] == 0, f"{rows[0][0]} linha(s) sem vinculo em N:N")
    )
    falhas += not check(
        db, "Unique (id_profissional, area) preservado",
        "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='corpo_clinico' AND indexdef ILIKE '%UNIQUE%(id_profissional, area)%'",
        lambda rows: (len(rows) == 1, "ok" if rows else "ausente")
    )

    print("\n== Migration 0098: cron_configs ==")
    falhas += not check(
        db, "Seed integrador 3 habilitado 23:01",
        "SELECT enabled, horario, rotina FROM cron_configs WHERE id_integrador=3",
        lambda rows: (bool(rows) and rows[0][0] is True and str(rows[0][1]).startswith("23:01"), f"seed={rows[0] if rows else 'ausente'}")
    )

    print("\n== Migration 0099: views retrocompat removidas ==")
    falhas += not check(
        db, "worker.convenios / worker.convenio_operacoes nao existem",
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='worker' AND table_name IN ('convenios','convenio_operacoes')",
        lambda rows: (len(rows) == 0, f"{len(rows)} objeto(s) restante(s): {[r[0] for r in rows]}")
    )
    falhas += not check(
        db, "worker.integradores preservada",
        "SELECT 1 FROM information_schema.tables WHERE table_schema='worker' AND table_name='integradores'",
        lambda rows: (len(rows) == 1, "ok" if rows else "AUSENTE - nao aplicar 0099!")
    )

    print("\n== Migration 0100: trigger multiprestador ==")
    falhas += not check(
        db, "func_vincula_guia usa v_multiprestador",
        "SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname='func_vincula_guia_a_agendamento'",
        lambda rows: (bool(rows) and "v_multiprestador" in rows[0][0], "regra presente" if rows and "v_multiprestador" in rows[0][0] else "trigger antiga!")
    )

    db.close()
    print(f"\n{'='*50}")
    if falhas:
        print(f"RESULTADO: {falhas} verificacao(oes) FALHARAM")
        sys.exit(1)
    print("RESULTADO: todas as verificacoes OK")


if __name__ == "__main__":
    main()
