from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
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

    return {
        "token": user.api_key, # Simple token for now, or could use JWT
        "username": user.username,
        "validade": user.validade,
        "is_admin": user.is_admin
    }

@router.get("/users")
def list_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem listar usuários.")
    
    users = db.query(User).filter(User.status == "Ativo").order_by(User.username).all()
    return [{"id": u.id, "username": u.username} for u in users]
