from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User
from pydantic import BaseModel

from dependencies import get_current_user

router = APIRouter()

class LoginRequest(BaseModel):
    access_key: str

@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.api_key == request.access_key).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave de acesso inválida."
        )
    

    # Check validity
    if user.validade and user.validade < datetime.now().date():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave está vencida. Contratar nova."
        )

    import jwt
    from dependencies import JWT_SECRET, JWT_ALGORITHM
    
    payload = {
        "sub": str(user.id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": datetime.now(timezone.utc),
        "username": user.username,
        "is_admin": user.is_admin
    }
    jwt_token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "token": jwt_token,
        "username": user.username,
        "validade": user.validade.isoformat() if user.validade else None,
        "is_admin": user.is_admin
    }

@router.get("/users")
def list_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem listar usuários.")
    
    users = db.query(User).filter(User.status == "Ativo").order_by(User.username).all()
    return [{"id": u.id, "username": u.username} for u in users]

from typing import Optional

class CreateUserRequest(BaseModel):
    username: str
    validade: Optional[str] = None  # YYYY-MM-DD
    is_admin: bool = False
    permitir_protocolo: bool = False
    status: str = "Ativo"

class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    validade: Optional[str] = None  # YYYY-MM-DD
    is_admin: Optional[bool] = None
    permitir_protocolo: Optional[bool] = None
    status: Optional[str] = None

@router.get("/admin/users")
def admin_list_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem gerenciar usuários.")
    
    users = db.query(User).order_by(User.username).all()
    
    return [
        {
            "id": u.id,
            "username": u.username,
            "api_key_masked": f"{u.api_key[:8]}..." if u.api_key else "",
            "validade": u.validade.isoformat() if u.validade else None,
            "status": u.status,
            "is_admin": u.is_admin,
            "permitir_protocolo": u.permitir_protocolo,
            "created_at": u.created_at.isoformat() if u.created_at else None
        }
        for u in users
    ]

@router.post("/admin/users")
def admin_create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem gerenciar usuários.")
        
    existing = db.query(User).filter(User.username == request.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Este nome de usuário já está cadastrado.")
        
    import secrets
    from datetime import datetime
    
    new_api_key = secrets.token_urlsafe(32)
    
    validade_date = None
    if request.validade:
        try:
            validade_date = datetime.strptime(request.validade, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data de validade inválido. Use YYYY-MM-DD.")
            
    user = User(
        username=request.username,
        api_key=new_api_key,
        status=request.status,
        is_admin=request.is_admin,
        permitir_protocolo=request.permitir_protocolo,
        validade=validade_date
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "validade": user.validade.isoformat() if user.validade else None,
            "status": user.status,
            "is_admin": user.is_admin,
            "permitir_protocolo": user.permitir_protocolo
        },
        "api_key": new_api_key
    }

@router.put("/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem gerenciar usuários.")
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        
    from datetime import datetime
    
    if request.username is not None:
        if request.username != user.username:
            existing = db.query(User).filter(User.username == request.username).first()
            if existing:
                raise HTTPException(status_code=400, detail="Este nome de usuário já está em uso.")
            user.username = request.username
            
    if request.status is not None:
        user.status = request.status
        
    if request.is_admin is not None:
        user.is_admin = request.is_admin
        
    if request.permitir_protocolo is not None:
        user.permitir_protocolo = request.permitir_protocolo
        
    if request.validade is not None:
        if request.validade == "":
            user.validade = None
        else:
            try:
                user.validade = datetime.strptime(request.validade, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Formato de data de validade inválido. Use YYYY-MM-DD.")
                
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "username": user.username,
        "validade": user.validade.isoformat() if user.validade else None,
        "status": user.status,
        "is_admin": user.is_admin,
        "permitir_protocolo": user.permitir_protocolo
    }

@router.post("/admin/users/{user_id}/regenerate-key")
def admin_regenerate_key(
    user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem gerenciar usuários.")
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        
    import secrets
    new_api_key = secrets.token_urlsafe(32)
    user.api_key = new_api_key
    
    db.commit()
    
    return {
        "message": "Nova chave de acesso gerada com sucesso.",
        "api_key": new_api_key
    }
