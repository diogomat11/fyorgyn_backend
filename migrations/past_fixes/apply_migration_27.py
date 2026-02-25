import os
import sys
from sqlalchemy import text
from database import SessionLocal

def run_migration():
    db = SessionLocal()
    try:
        with open("migrations/0027_convenio_operacoes.sql", "r", encoding="utf-8") as f:
            sql = f.read()
        db.execute(text(sql))
        db.commit()
        print("Migration 0027 applied successfully.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    run_migration()
