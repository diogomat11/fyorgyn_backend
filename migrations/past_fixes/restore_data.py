from database import engine
from sqlalchemy import text

def restore_data():
    with engine.connect() as con:
        print("Restoring data...")
        
        # 1. Map Clinica Larissa to UNIMED GOIANIA (ID 3)
        res = con.execute(text("UPDATE users SET id_convenio = 3 WHERE id = 1 AND id_convenio IS NULL"))
        print(f"Updated {res.rowcount} users")
        
        # 2. Map carteirinhas with None to UNIMED GOIANIA (ID 3)
        res = con.execute(text("UPDATE carteirinhas SET id_convenio = 3 WHERE id_convenio IS NULL"))
        print(f"Updated {res.rowcount} carteirinhas")
        
        con.commit()
        print("Data restoration complete.")

if __name__ == "__main__":
    restore_data()
