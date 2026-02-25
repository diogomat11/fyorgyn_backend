from database import engine
from sqlalchemy import text

def check():
    with engine.connect() as con:
        user = con.execute(text("SELECT id, username, id_convenio FROM users")).fetchall()
        print("USERS:", user)
        
        carts_count = con.execute(text("SELECT id_convenio, COUNT(*) FROM carteirinhas GROUP BY id_convenio")).fetchall()
        print("CARTEIRINHAS COUNT BY CONVENIO:", carts_count)
        
        # Check samples for convenio 3
        carts_sample = con.execute(text("SELECT id, carteirinha, id_convenio FROM carteirinhas WHERE id_convenio = 3 LIMIT 5")).fetchall()
        print("UNIMED CARTS SAMPLES:", carts_sample)

if __name__ == "__main__":
    check()
