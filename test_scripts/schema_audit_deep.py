import os
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

def schema_audit():
    load_dotenv('./.env')
    engine = create_engine(os.getenv('DATABASE_URL'))
    inspector = inspect(engine)
    
    print("TABLE: jobs")
    cols = inspector.get_columns('jobs')
    for c in cols:
        print(f"  - {c['name']} (Type: {c['type']})")
    
    # Check raw row again with column names
    col_names = [c['name'] for c in cols]
    with engine.connect() as con:
        raw = con.execute(text("SELECT * FROM jobs WHERE id = 1")).fetchone()
        if raw:
            mapped = dict(zip(col_names, raw))
            print("\nJOB 1 MAPPED:", mapped)

if __name__ == "__main__":
    schema_audit()
