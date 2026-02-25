from database import SessionLocal
from models import User, Convenio

def link_all_convenios():
    db = SessionLocal()
    try:
        users = db.query(User).all()
        convenios = db.query(Convenio).all()
        
        for user in users:
            # Add all convenios to user's convenio_rel
            for c in convenios:
                if c not in user.convenio_rel:
                    user.convenio_rel.append(c)
        
        db.commit()
        print(f"Successfully linked {len(convenios)} convenios to {len(users)} users.")
        
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    link_all_convenios()
