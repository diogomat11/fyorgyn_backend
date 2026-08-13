import os
import jwt
from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from database import get_db
from models import User
from datetime import datetime, timedelta

JWT_SECRET = os.getenv("JWT_SECRET", "fyorgyn_jwt_secret_2025_secure_key")
JWT_ALGORITHM = "HS256"

async def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação ausente."
        )
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de token inválido. Use 'Bearer <token>'."
        )
    
    token = authorization.split(" ")[1]
    
    # Tentar JWT primeiro
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            if user.status != "Ativo":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuário inativo."
                )
            if user.validade and user.validade < datetime.now().date():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Chave de acesso vencida."
                )
            return user
    except (jwt.PyJWTError, ValueError, TypeError):
        pass
        
    # Fallback: API Key
    user = db.query(User).filter(User.api_key == token).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou usuário não encontrado."
        )
        
    if user.status != "Ativo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo."
        )
        
    if user.validade and user.validade < datetime.now().date():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave de acesso vencida."
        )
        
    return user

def get_allowed_convenio_ids(user: User):
    """Retorna a lista de IDs de convênio permitidos para este usuário."""
    if user.convenio_rel:
        return [c.id_convenio for c in user.convenio_rel]
    if user.id_convenio: # Fallback legado
        return [user.id_convenio]
    return [] # Se vazio, assumimos Admin para rotas que verificam 'if allowed_ids'

async def get_protocolo_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    user = await get_current_user(authorization, db)
    if not user.permitir_protocolo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso não autorizado ao módulo de protocolo."
        )
    return user

def get_current_worker_key(authorization: str = Header(None)) -> str:
    """Extrai o worker_key do payload JWT se presente."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("worker_key")
    except Exception:
        return None

def require_perfil(*perfis: str):
    """Dependency factory que valida se o usuário possui um dos perfis exigidos."""
    async def _dependency(user: User = Depends(get_current_user)):
        if user.is_admin:
            return user
        user_perfil = getattr(user, "perfil", "gestor") or "gestor"
        if user_perfil in perfis or user_perfil == "gestor":
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acesso negado. Perfil '{user_perfil}' não tem permissão para esta operação."
        )
    return _dependency


def get_effective_user_id(user: User) -> int:
    """Retorna o ID do Gestor (parent_user_id) caso o usuário seja um sub-usuário, garantindo multi-tenancy correto."""
    if user.is_admin:
        return user.id
    return user.parent_user_id if user.parent_user_id else user.id


def check_module_permission(user: User, modulo: str, acao: str = "visualizar") -> bool:
    """Valida se o usuário possui a permissão no módulo e ação especificados."""
    if user.is_admin or (getattr(user, "perfil", "") == "gestor"):
        return True
    
    perm = getattr(user, "permissoes", {}) or {}
    if not perm:
        # Se não houver permissão customizada, usa default por perfil
        perfil = getattr(user, "perfil", "agendamento")
        if perfil == "agendamento":
            if modulo == "agendamentos":
                return True
            if modulo == "workflow_faturamento":
                return acao in ["visualizar", "filtrar", "sincronizar"]
            return acao == "visualizar"
        elif perfil == "faturamento":
            return True
        elif perfil == "supervisor":
            return True
        return False
        
    mod_perm = perm.get(modulo, {})
    if isinstance(mod_perm, bool):
        return mod_perm
    if isinstance(mod_perm, dict):
        return bool(mod_perm.get(acao, False))
    return False


