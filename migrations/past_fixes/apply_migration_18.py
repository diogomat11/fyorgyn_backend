import os
import sys

# Add the backend directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import engine, SessionLocal
from models import Base, AreaAtuacao, Conselho

def apply_migration():
    print("Creating new tables...")
    # This will safely create any newly defined tables in models.py
    Base.metadata.create_all(bind=engine)
    print("Tables created.")

    # Apply seeds
    db = SessionLocal()
    try:
        # 1. Seed Areas de Atuacao
        areas_data = [
            {"nome": "PSICOLOGIA", "cbo": "251510"},
            {"nome": "FONOAUDIOLOGIA", "cbo": "223910"},
            {"nome": "TERAPIA OCUPACIONAL", "cbo": "223905"},
            {"nome": "FISIOTERAPIA", "cbo": "223605"},
            {"nome": "PSICOMOTRICIDADE", "cbo": "223915"},
            {"nome": "PSICOPEDAGOGIA", "cbo": "239425"},
            {"nome": "MUSICOTERAPIA", "cbo": "226605"}
        ]
        
        for area in areas_data:
            existing = db.query(AreaAtuacao).filter(AreaAtuacao.nome == area["nome"]).first()
            if not existing:
                db.add(AreaAtuacao(nome=area["nome"], cbo=area["cbo"]))
                print(f"Added Area: {area['nome']}")

        # 2. Seed Conselhos
        conselhos_data = ["CRM", "CRP", "CREFITO", "CREFONO", "AGMT", "CRO"]
        
        for conselho in conselhos_data:
            existing = db.query(Conselho).filter(Conselho.nome_conselho == conselho).first()
            if not existing:
                db.add(Conselho(nome_conselho=conselho))
                print(f"Added Conselho: {conselho}")

        db.commit()
        print("Seeds applied successfully.")
    except Exception as e:
        print(f"Error applying seeds: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    apply_migration()
