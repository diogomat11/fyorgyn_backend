from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Integrador, IntegradorOperacao, Convenio, User,
    WorkerConfig, WorkerApiKey
)
from pydantic import BaseModel
from typing import List, Optional
from dependencies import get_current_user
from datetime import datetime

router = APIRouter(
    prefix="/integradores",
    tags=["integradores"]
)

# --- Schemas Pydantic ---
class IntegradorBase(BaseModel):
    nome: str
    sigla: Optional[str] = None
    tipo_operacao: str = "convenio"  # 'convenio' ou 'agendamento'
    ativo: bool = True
    timeout_captura: bool = False

class IntegradorCreate(IntegradorBase):
    id_convenio: Optional[int] = None

class IntegradorUpdate(BaseModel):
    nome: Optional[str] = None
    sigla: Optional[str] = None
    tipo_operacao: Optional[str] = None
    ativo: Optional[bool] = None
    timeout_captura: Optional[bool] = None

class IntegradorOperacaoCreate(BaseModel):
    rotina: str
    descricao: Optional[str] = None
    tipo_processamento: str = "local"  # 'local', 'server', 'remoto'
    ativo: bool = True
    ordem: int = 0

class IntegradorOperacaoUpdate(BaseModel):
    descricao: Optional[str] = None
    tipo_processamento: Optional[str] = None
    ativo: Optional[bool] = None
    ordem: Optional[int] = None

class WorkerApiKeyCreate(BaseModel):
    api_key: str
    user_id: int
    tipo_processamento: str = "local"
    descricao: Optional[str] = None

class WorkerConfigUpdate(BaseModel):
    max_servers: int = 7
    dispatch_stagger_seconds: int = 15


# --- Endpoints ---

@router.get("/")
def list_integradores(
    ativo_only: bool = False,
    tipo_operacao: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lista todos os integradores com suas operações e configurações."""
    from models import WorkerIntegrador, WorkerIntegradorOperacao
    query = db.query(WorkerIntegrador)
    if ativo_only:
        query = query.filter(WorkerIntegrador.ativo == True)
    if tipo_operacao:
        query = query.filter(WorkerIntegrador.tipo_operacao == tipo_operacao)
    
    integradores = query.order_by(WorkerIntegrador.id_integrador.asc()).all()
    
    result = []
    for ing in integradores:
        ops = db.query(WorkerIntegradorOperacao).filter(WorkerIntegradorOperacao.id_integrador == ing.id_integrador).all()
        result.append({
            "id_integrador": ing.id_integrador,
            "nome": ing.nome,
            "sigla": ing.sigla,
            "tipo_operacao": ing.tipo_operacao,
            "ativo": ing.ativo,
            "timeout_captura": getattr(ing, "timeout_captura", False) or False,
            "created_at": ing.created_at,
            "operacoes": [
                {
                    "id": op.id,
                    "id_integrador": op.id_integrador,
                    "rotina": op.rotina,
                    "descricao": op.descricao,
                    "tipo_processamento": op.tipo_processamento,
                    "ativo": op.ativo,
                    "modo_execucao": getattr(op, "modo_execucao", "automatico")
                } for op in ops
            ]
        })
    return result


@router.post("/")
def create_integrador(
    data: IntegradorCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria um novo integrador no schema worker (Admin)."""
    from models import WorkerIntegrador
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail="Apenas administradores podem criar integradores")
    
    ing = WorkerIntegrador(
        nome=data.nome,
        sigla=data.sigla,
        tipo_operacao=data.tipo_operacao,
        ativo=data.ativo,
        timeout_captura=data.timeout_captura
    )
    db.add(ing)
    db.commit()
    db.refresh(ing)
    return ing


@router.put("/config")
def update_worker_config(
    data: WorkerConfigUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Atualiza a configuração global de workers (Admin)."""
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail="Apenas administradores podem alterar configurações globais")
    
    cfg_max = db.query(WorkerConfig).filter(WorkerConfig.chave == "max_servers").first()
    if not cfg_max:
        cfg_max = WorkerConfig(chave="max_servers", valor=str(data.max_servers))
        db.add(cfg_max)
    else:
        cfg_max.valor = str(data.max_servers)
        
    cfg_stagger = db.query(WorkerConfig).filter(WorkerConfig.chave == "dispatch_stagger_seconds").first()
    if not cfg_stagger:
        cfg_stagger = WorkerConfig(chave="dispatch_stagger_seconds", valor=str(data.dispatch_stagger_seconds))
        db.add(cfg_stagger)
    else:
        cfg_stagger.valor = str(data.dispatch_stagger_seconds)
        
    db.commit()
    return {"status": "success", "max_servers": data.max_servers, "dispatch_stagger_seconds": data.dispatch_stagger_seconds}


@router.put("/operacoes/{op_id}")
def update_operacao_integrador(
    op_id: int,
    data: IntegradorOperacaoUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Atualiza uma operação de integrador (Admin)."""
    from models import WorkerIntegradorOperacao
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail="Apenas administradores podem atualizar operações")
    
    op = db.query(WorkerIntegradorOperacao).filter(WorkerIntegradorOperacao.id == op_id).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    
    if data.descricao is not None: op.descricao = data.descricao
    if data.tipo_processamento is not None: op.tipo_processamento = data.tipo_processamento
    if data.ativo is not None: op.ativo = data.ativo
    
    db.commit()
    db.refresh(op)
    return op


@router.put("/{id_integrador}")
def update_integrador(
    id_integrador: int,
    data: IntegradorUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Atualiza dados do integrador e timeout de captura (Admin)."""
    from models import WorkerIntegrador
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail="Apenas administradores podem atualizar integradores")
    
    ing = db.query(WorkerIntegrador).filter(WorkerIntegrador.id_integrador == id_integrador).first()
    if not ing:
        raise HTTPException(status_code=404, detail="Integrador não encontrado")
    
    if data.nome is not None: ing.nome = data.nome
    if data.sigla is not None: ing.sigla = data.sigla
    if data.tipo_operacao is not None: ing.tipo_operacao = data.tipo_operacao
    if data.ativo is not None: ing.ativo = data.ativo
    if data.timeout_captura is not None: ing.timeout_captura = data.timeout_captura
    
    db.commit()
    db.refresh(ing)
    return ing


@router.delete("/{id_integrador}")
def delete_integrador(
    id_integrador: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Desativa um integrador (Admin)."""
    from models import WorkerIntegrador
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail="Apenas administradores podem desativar integradores")
    
    ing = db.query(WorkerIntegrador).filter(WorkerIntegrador.id_integrador == id_integrador).first()
    if not ing:
        raise HTTPException(status_code=404, detail="Integrador não encontrado")
    
    ing.ativo = False
    db.commit()
    return {"status": "success", "message": f"Integrador {id_integrador} desativado"}


# === Operações por Integrador ===

@router.get("/{id_integrador}/operacoes")
def list_operacoes_integrador(
    id_integrador: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lista operações de um integrador."""
    from models import WorkerIntegradorOperacao
    ops = db.query(WorkerIntegradorOperacao).filter(WorkerIntegradorOperacao.id_integrador == id_integrador).all()
    return ops


@router.post("/{id_integrador}/operacoes")
def create_operacao_integrador(
    id_integrador: int,
    data: IntegradorOperacaoCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria uma nova operação para um integrador (Admin)."""
    from models import WorkerIntegrador, WorkerIntegradorOperacao
    if not getattr(current_user, 'is_admin', False):
        raise HTTPException(status_code=403, detail="Apenas administradores podem adicionar operações")
    
    ing = db.query(WorkerIntegrador).filter(WorkerIntegrador.id_integrador == id_integrador).first()
    if not ing:
        raise HTTPException(status_code=404, detail="Integrador não encontrado")
    
    op = WorkerIntegradorOperacao(
        id_integrador=id_integrador,
        rotina=data.rotina,
        descricao=data.descricao,
        tipo_processamento=data.tipo_processamento,
        ativo=data.ativo
    )
    db.add(op)
    db.commit()
    db.refresh(op)
    return op




# === Worker API Keys ===

@router.get("/worker-keys")
def list_worker_keys(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lista chaves API de workers registradas com respectivo código de login worker_key."""
    import secrets
    from models import UserWorker

    keys = db.query(WorkerApiKey).all()
    result = []
    for k in keys:
        uw = db.query(UserWorker).filter(UserWorker.user_id == k.user_id, UserWorker.ativo == True).first()
        if not uw:
            w_key = f"WRK-{secrets.token_hex(3).upper()}"
            uw = UserWorker(user_id=k.user_id, worker_key=w_key, descricao=k.descricao or "Worker Auto Key", ativo=True)
            db.add(uw)
            db.commit()
            db.refresh(uw)
        
        result.append({
            "id": k.id,
            "api_key": k.api_key,
            "user_id": k.user_id,
            "worker_key": uw.worker_key,
            "tipo_processamento": k.tipo_processamento,
            "descricao": k.descricao,
            "ativo": k.ativo,
            "created_at": k.created_at
        })
    return result


@router.post("/worker-keys")
def create_worker_key(
    data: WorkerApiKeyCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria uma nova chave API para um worker e gera a worker_key de login (Admin)."""
    import secrets
    from models import UserWorker

    wk = WorkerApiKey(
        api_key=data.api_key,
        user_id=data.user_id,
        tipo_processamento=data.tipo_processamento,
        descricao=data.descricao
    )
    db.add(wk)
    db.commit()
    db.refresh(wk)

    # Automatically generate worker_key for user login validation
    uw = db.query(UserWorker).filter(UserWorker.user_id == data.user_id, UserWorker.ativo == True).first()
    if not uw:
        w_key = f"WRK-{secrets.token_hex(3).upper()}"
        uw = UserWorker(user_id=data.user_id, worker_key=w_key, descricao=data.descricao or "Local Worker Key", ativo=True)
        db.add(uw)
        db.commit()
        db.refresh(uw)

    return {
        "id": wk.id,
        "api_key": wk.api_key,
        "user_id": wk.user_id,
        "worker_key": uw.worker_key,
        "tipo_processamento": wk.tipo_processamento,
        "descricao": wk.descricao,
        "ativo": wk.ativo
    }



# === Global Worker Config ===

@router.get("/config")
def get_worker_config(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Retorna a configuração global de workers."""
    cfgs = db.query(WorkerConfig).all()
    result = {c.chave: c.valor for c in cfgs}
    return {
        "max_servers": int(result.get("max_servers", 7)),
        "dispatch_stagger_seconds": int(result.get("dispatch_stagger_seconds", 15))
    }


