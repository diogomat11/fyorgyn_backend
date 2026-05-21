import os
from database import engine
from sqlalchemy import text
from sqlalchemy.orm import Session
from models import LoteConvenio, FaturamentoLote

with Session(engine) as session:
    # faturamento_lotes.id_lote currently contains the IPASGO 'numero_lote' 
    # because it was simply renamed from 'loteId'.
    # We must remap them to the actual 'id_lote' of lotes_convenio.
    
    lotes = session.query(LoteConvenio).all()
    for lote in lotes:
        if lote.numero_lote:
            # We assume faturamento_lotes.id_lote has the numero_lote value right now
            session.execute(
                text("UPDATE faturamento_lotes SET id_lote = :real_id WHERE id_lote = :numero"),
                {"real_id": lote.id_lote, "numero": lote.numero_lote}
            )
    
    # Optional: nullify any id_lote that still has a huge number (meaning it's an orphaned IPASGO loteId)
    # But since it's constrained by FK, Supabase might have already set them to NULL if the FK was strictly enforced during the ALTER TABLE!
    # Wait, the migration added the FK: "ALTER TABLE faturamento_lotes ADD CONSTRAINT fk_faturamento_lotes_id_lote FOREIGN KEY (id_lote) REFERENCES lotes_convenio(id_lote) ON DELETE SET NULL;"
    # If the database enforced the FK immediately, then any faturamento_lote with a 'loteId' not present in lotes_convenio(id_lote) might have thrown an error or been deleted. Let's check.
    
    session.commit()
    print("Data mapping complete.")
