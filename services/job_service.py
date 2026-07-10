import os
import requests
import json
import random
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from models import Carteirinha, Convenio, UserConvenio, Job

BACKEND_WORKER_URL = os.getenv("BACKEND_WORKER_URL", "http://localhost:8001")
BPO_API_KEY = os.getenv("BPO_API_KEY", "bpo_secret_api_key_2026")
MY_WEBHOOK_URL = os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")

def _send_jobs_to_worker(jobs_payload: List[dict]) -> int:
    if not jobs_payload:
        return 0
    headers = {
        "Authorization": f"Bearer {BPO_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{BACKEND_WORKER_URL}/api/v1/jobs/batch"
    try:
        response = requests.post(url, json={"jobs": jobs_payload}, headers=headers, timeout=20)
        if response.status_code == 201:
            return len(response.json())
        else:
            raise HTTPException(status_code=502, detail=f"Erro ao enviar jobs ao backend_worker: {response.text}")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=502, detail=f"Falha de conexão com backend_worker: {str(e)}")

def create_jobs_bulk(db: Session, carteirinha_ids: List[int], id_convenio: Optional[int] = None, rotina: Optional[str] = None, params: Optional[str] = None, user_id: Optional[int] = None) -> int:
    """
    Creates multiple jobs for existing carteirinhas and sends them to backend_worker.
    """
    if not carteirinha_ids:
        return 0
        
    carteirinhas = db.query(Carteirinha).filter(Carteirinha.id.in_(carteirinha_ids)).all()
    if not carteirinhas:
        return 0

    base_p_dict = json.loads(params) if params else {}
    jobs_payload = []
    
    for cart in carteirinhas:
        p_dict = base_p_dict.copy()
        
        # Injetar dados do paciente
        p_dict["Paciente"] = p_dict.get("Paciente") or cart.paciente or ""
        p_dict["Carteira"] = p_dict.get("Carteira") or cart.carteirinha or ""
        p_dict["TarjaMagnetica"] = p_dict.get("TarjaMagnetica") or getattr(cart, "tarja_magnetica", "") or ""
        
        # Enriquecer credenciais do convênio
        target_conv_id = id_convenio or cart.id_convenio
        if target_conv_id and user_id:
            uconv = db.query(UserConvenio).filter(
                UserConvenio.user_id == user_id,
                UserConvenio.id_convenio == target_conv_id
            ).first()
            if uconv:
                p_dict["login"] = p_dict.get("login") or uconv.login
                p_dict["senha_criptografada"] = p_dict.get("senha_criptografada") or uconv.senha_criptografada
                p_dict["cod_prestador"] = p_dict.get("cod_prestador") or uconv.cod_prestador
                p_dict["login_fat"] = p_dict.get("login_fat") or uconv.login_fat
                p_dict["senha_fat_criptografada"] = p_dict.get("senha_fat_criptografada") or uconv.senha_fat_criptografada
                
        # Injetar webhook_url
        p_dict["webhook_url"] = MY_WEBHOOK_URL
        
        job_data = {
            "carteirinha_id": cart.id,
            "id_convenio": target_conv_id,
            "rotina": rotina,
            "priority": 0,
            "params": p_dict,
            "max_attempts": 3
        }
        jobs_payload.append(job_data)
        
    return _send_jobs_to_worker(jobs_payload)

def create_all_jobs(db: Session, id_convenio: Optional[int] = None, rotina: Optional[str] = None, params: Optional[str] = None, user_id: Optional[int] = None) -> int:
    """
    Creates jobs for ALL non-temporary carteirinhas and sends them to backend_worker.
    """
    query = db.query(Carteirinha).filter(Carteirinha.is_temporary == False)
    if id_convenio is not None:
        query = query.filter(Carteirinha.id_convenio == id_convenio)
    
    all_carteirinhas = query.all()
    if not all_carteirinhas:
        return 0
        
    base_p_dict = json.loads(params) if params else {}
    jobs_payload = []
    
    for cart in all_carteirinhas:
        p_dict = base_p_dict.copy()
        
        p_dict["Paciente"] = p_dict.get("Paciente") or cart.paciente or ""
        p_dict["Carteira"] = p_dict.get("Carteira") or cart.carteirinha or ""
        p_dict["TarjaMagnetica"] = p_dict.get("TarjaMagnetica") or getattr(cart, "tarja_magnetica", "") or ""
        
        target_conv_id = id_convenio or cart.id_convenio
        if target_conv_id and user_id:
            uconv = db.query(UserConvenio).filter(
                UserConvenio.user_id == user_id,
                UserConvenio.id_convenio == target_conv_id
            ).first()
            if uconv:
                p_dict["login"] = p_dict.get("login") or uconv.login
                p_dict["senha_criptografada"] = p_dict.get("senha_criptografada") or uconv.senha_criptografada
                p_dict["cod_prestador"] = p_dict.get("cod_prestador") or uconv.cod_prestador
                p_dict["login_fat"] = p_dict.get("login_fat") or uconv.login_fat
                p_dict["senha_fat_criptografada"] = p_dict.get("senha_fat_criptografada") or uconv.senha_fat_criptografada
                
        p_dict["webhook_url"] = MY_WEBHOOK_URL
        
        job_data = {
            "carteirinha_id": cart.id,
            "id_convenio": target_conv_id,
            "rotina": rotina,
            "priority": 0,
            "params": p_dict,
            "max_attempts": 3
        }
        jobs_payload.append(job_data)
        
    return _send_jobs_to_worker(jobs_payload)

def create_temp_job(db: Session, carteirinha: str, paciente: str, id_convenio: Optional[int] = None, rotina: Optional[str] = None, params: Optional[str] = None, user_id: Optional[int] = None) -> int:
    """
    Creates a temporary patient and job, then sends it to backend_worker.
    """
    query = db.query(Carteirinha).filter(Carteirinha.carteirinha == carteirinha)
    if user_id is not None:
        query = query.filter(Carteirinha.user_id == user_id)
    existing = query.first()
    cart_id = None
    
    if existing:
        if existing.is_temporary:
            existing.expires_at = datetime.utcnow() + timedelta(hours=1)
            existing.paciente = paciente
        cart_id = existing.id
    else:
        fake_id_paciente = random.randint(900000, 999999) 
        new_cart = Carteirinha(
            carteirinha=carteirinha,
            paciente=paciente,
            id_paciente=fake_id_paciente,
            is_temporary=True,
            expires_at=datetime.utcnow() + timedelta(hours=1),
            user_id=user_id
        )
        db.add(new_cart)
        db.flush()
        cart_id = new_cart.id
    
    if not rotina:
        rotina = "consulta_guias" if id_convenio == 3 or id_convenio == 2 else None
        
    p_dict = json.loads(params) if params else {}
    p_dict["Paciente"] = p_dict.get("Paciente") or paciente
    p_dict["Carteira"] = p_dict.get("Carteira") or carteirinha
    
    if id_convenio and user_id:
        uconv = db.query(UserConvenio).filter(
            UserConvenio.user_id == user_id,
            UserConvenio.id_convenio == id_convenio
        ).first()
        if uconv:
            p_dict["login"] = p_dict.get("login") or uconv.login
            p_dict["senha_criptografada"] = p_dict.get("senha_criptografada") or uconv.senha_criptografada
            p_dict["cod_prestador"] = p_dict.get("cod_prestador") or uconv.cod_prestador
            p_dict["login_fat"] = p_dict.get("login_fat") or uconv.login_fat
            p_dict["senha_fat_criptografada"] = p_dict.get("senha_fat_criptografada") or uconv.senha_fat_criptografada
            
    p_dict["webhook_url"] = MY_WEBHOOK_URL
    
    job_data = {
        "carteirinha_id": cart_id,
        "id_convenio": id_convenio,
        "rotina": rotina,
        "priority": 0,
        "params": p_dict,
        "max_attempts": 3
    }
    
    _send_jobs_to_worker([job_data])
    return 1


