from sqlalchemy.orm import Session
from models import BaseGuia, PeiTemp, PatientPei
from datetime import timedelta, date

def update_patient_pei(db: Session, carteirinha_id: int, codigo_terapia: str, guia_instance: BaseGuia = None):
    """
    Recalculates and updates the PatientPei record for a specific patient and therapy.
    Triggered automatically by changes in BaseGuia or PeiTemp.
    """
    # 0. Check for Convenio Restriction (Only Unimed)
    # Get id_convenio from carteirinha
    from models import Carteirinha, Convenio
    cart = db.query(Carteirinha).filter(Carteirinha.id == carteirinha_id).first()
    if not cart:
        return
    
    # Dynamic check: Find ID of UNIMED
    unimed = db.query(Convenio).filter(Convenio.nome.ilike("%UNIMED%")).first()
    unimed_id = unimed.id_convenio if unimed else 2 # Default to 2 based on seed
    
    if cart.id_convenio != unimed_id:
        # PEI not applicable to other convenios
        return

    # 1. Find the latest Guia for this patient + therapy
    # Logic: Newest data_autorizacao, then newest ID (tie-breaker)
    # Note: If valid_instance is provided (from before_flush), we must consider it.
    
    db_latest = db.query(BaseGuia).filter(
        BaseGuia.carteirinha_id == carteirinha_id,
        BaseGuia.codigo_terapia == codigo_terapia,
        BaseGuia.status_guia.ilike('Autorizado')
    ).order_by(BaseGuia.data_autorizacao.desc(), BaseGuia.id.desc()).first()
    
    latest_guia = db_latest

    if guia_instance and getattr(guia_instance, 'status_guia', 'Autorizado').upper() == 'AUTORIZADO':
        candidate_list = []
        if db_latest:
            candidate_list.append(db_latest)
        
        if guia_instance not in candidate_list:
            candidate_list.append(guia_instance)
            
        def sort_key(g):
            d = g.data_autorizacao or date.min
            i = g.id if g.id is not None else float('inf')
            return (d, i)
            
        candidate_list.sort(key=sort_key, reverse=True)
        latest_guia = candidate_list[0] if candidate_list else None

    if not latest_guia:
        # Remove PEI if no Autorizado guia left
        existing_pei = db.query(PatientPei).filter(
            PatientPei.carteirinha_id == carteirinha_id,
            PatientPei.codigo_terapia == codigo_terapia
        ).first()
        if existing_pei:
            db.delete(existing_pei)
        return


    # 2. Check for Manual Overrides (PeiTemp)
    override = db.query(PeiTemp).filter(PeiTemp.base_guia_id == latest_guia.id).first()
    
    # If not found in DB, check session.new (unflushed inserts)
    if not override:
        for obj in db.new:
            if isinstance(obj, PeiTemp) and obj.base_guia_id == latest_guia.id:
                override = obj
                break
                
    # Also check dirty? (Unlikely to change ID, but maybe value)
    if override and override in db.dirty:
        # It's already the object we want, presumably up to date.
        pass

    status = "Pendente"
    pei_semanal = 0.0
    validade = None
    
    if latest_guia.data_autorizacao:
        # Validity Rule: Autorizacao + 180 days (approx 6 months)
        validade = latest_guia.data_autorizacao + timedelta(days=180)
    


    if override:
        # Priority 1: Manual Override
        pei_semanal = float(override.pei_semanal)
        status = "Validado" 
    else:
        # Priority 2: Automatic Calculation
        if latest_guia.qtde_solicitada:
             # Rule: Quantity / 16
             val = float(latest_guia.qtde_solicitada) / 16.0
             pei_semanal = val
             
             # If whole number, auto-validate. Else pending manual review.
             if val.is_integer():
                 status = "Validado"
             else:
                 status = "Pendente"
        else:
            pei_semanal = 0.0
            status = "Pendente"
            
    # 3. Update or Create PatientPei Record
    patient_pei = db.query(PatientPei).filter(
        PatientPei.carteirinha_id == carteirinha_id,
        PatientPei.codigo_terapia == codigo_terapia
    ).first()

    if not patient_pei:
        patient_pei = PatientPei(
            carteirinha_id=carteirinha_id,
            codigo_terapia=codigo_terapia
        )
        db.add(patient_pei)
    
    patient_pei.base_guia_id = latest_guia.id
    patient_pei.pei_semanal = pei_semanal
    patient_pei.validade = validade
    patient_pei.status = status
    
    # We do NOT commit here because this function is called inside event listeners
    # or other transactions. The caller (SQLAlchemy session flush) handles the transaction.
    # However, for 'after_insert' events, the session might be in a specific state.
    # Usually, modifying specific objects in after_insert can be tricky.
    # A standard approach for 'after_flush' or 'after_insert' is using 'Session.object_session(obj)'.
