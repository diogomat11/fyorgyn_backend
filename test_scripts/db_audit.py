from database import engine
from sqlalchemy import text

def audit():
    with engine.connect() as con:
        print("--- USERS ---")
        users = con.execute(text("SELECT id, username, id_convenio FROM users")).fetchall()
        for u in users:
            print(f"User {u[0]}: {u[1]}, Conv: {u[2]}")

        print("\n--- CONVENIOS ---")
        convs = con.execute(text("SELECT id_convenio, nome FROM convenios")).fetchall()
        for c in convs:
            print(f"ID {c[0]}: {c[1]}")

        print("\n--- CARTEIRINHAS COUNT ---")
        carts = con.execute(text("SELECT id_convenio, COUNT(*) FROM carteirinhas GROUP BY id_convenio")).fetchall()
        for c in carts:
            print(f"Conv {c[0]}: {c[1]} carts")

        print("\n--- WORKERS ---")
        try:
            workers = con.execute(text("SELECT * FROM workers")).fetchall()
            for w in workers:
                print(w)
        except Exception as e:
            print(f"Error reading workers table: {e}")

if __name__ == "__main__":
    audit()
