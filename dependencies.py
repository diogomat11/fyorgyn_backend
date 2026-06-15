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
