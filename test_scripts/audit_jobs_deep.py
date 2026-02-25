from database import engine
from sqlalchemy import text, inspect

def audit_jobs():
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns('jobs')]
    print("JOBS TABLE COLUMNS:", columns)
    
    with engine.connect() as con:
        res = con.execute(text("SELECT * FROM jobs")).fetchall()
        print("TOTAL ROWS IN JOBS:", len(res))
        for row in res:
            # Map values to column names for clarity
            row_dict = dict(zip(columns, row))
            print(f"JOB ID {row_dict.get('id')}: {row_dict}")

if __name__ == "__main__":
    audit_jobs()
