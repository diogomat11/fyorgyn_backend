import os
import sys

# Add the backend directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import engine
from sqlalchemy import text

def add_status_guia_column():
    try:
        with engine.begin() as conn:
            # Check if column exists first (PostgreSQL)
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='base_guias' AND column_name='status_guia'"))
            if not result.fetchone():
                print("Adding 'status_guia' column to 'base_guias' table...")
                conn.execute(text("ALTER TABLE base_guias ADD COLUMN status_guia TEXT DEFAULT 'Autorizado'"))
                print("Migration successful.")
            else:
                print("'status_guia' column already exists.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    add_status_guia_column()
