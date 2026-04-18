import os
from sqlalchemy import text
from database import engine

def run_migration():
    migration_path = os.path.join("migrations", "0042_ipasgo_guide_linking.sql")
    print(f"Executing {migration_path}...")
    
    with open(migration_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Split roughly by semicolon or execute as a whole block. 
    # Since it contains functions and triggers with $$ that contain semicolons, 
    # executing as a single text block is safer on raw connection.
    with engine.begin() as conn:
        try:
            conn.execute(text(sql_content))
            print("Migration executed successfully.")
        except Exception as e:
            print(f"Error executing migration: {e}")

if __name__ == "__main__":
    run_migration()
