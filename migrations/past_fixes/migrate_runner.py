import os
import sys
from sqlalchemy import text
from database import engine

def run_migrations():
    print("Running migrations...")
    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    
    # Order matters: 0001 then 0002
    files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".sql")])
    
    with engine.connect() as connection:
        # Create migration tracking table if not exists
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS migrations_log (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.commit()

        for file in files:
            # Check if already applied
            result = connection.execute(text("SELECT 1 FROM migrations_log WHERE filename = :f"), {"f": file})
            if result.fetchone():
                continue

            print(f"Executing {file}...")
            with open(os.path.join(migrations_dir, file), "r", encoding="utf-8") as f:
                sql = f.read()
                try:
                    # Execute as a single block (Postgres supports this)
                    connection.execute(text(sql))
                    # Record success
                    connection.execute(text("INSERT INTO migrations_log (filename) VALUES (:f)"), {"f": file})
                    connection.commit()
                    print(f"Finished {file}")
                except Exception as e:
                    import traceback
                    print(f"Error executing {file}:")
                    traceback.print_exc()
                    connection.rollback()
                    # Do not exit; let other potential migrations run or fail gracefully
                    
    print("Migrations completed.")
                    
    print("Migrations completed.")

if __name__ == "__main__":
    run_migrations()
