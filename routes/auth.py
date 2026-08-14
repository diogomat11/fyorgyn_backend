from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, UserWorker, UserUserConvenio, UserConvenio, UserIntegrador
from pydantic import BaseModel
from typing import Optional, List
import bcrypt
import secrets
import jwt

from dependencies import get_current_user, JWT_SECRET, JWT_ALGORITHM

router = APIRouter()

class LoginRequest(BaseModel):
    login: Optional[str] = None
    senha: Optional[str] = None
    worker_key: Optional[str] = None
    access_key: Optional[str] = None # Legacy fallback

@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = None
    # 1. Search by login & password
    if request.login and request.senha:
        user = db.query(User).filter(User.login == request.login).first()
        if not user:
            user = db.query(User).filter(User.api_key == request.login).first()
        
        if not user or not user.senha_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Login ou senha incorretos."
            )
        
        if not bcrypt.checkpw(request.senha.encode('utf-8'), user.senha_hash.encode('utf-8')):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Login ou senha incorretos."
            )
    # 2. Fallback to access_key
    elif request.access_key:
        user = db.query(User).filter(User.api_key == request.access_key.strip()).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Chave de acesso inválida."
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe login/senha ou a chave de acesso."
        )

    # Check status
    if user.status != "Ativo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo."
        )

    # Check validity
    if user.validade and user.validade < datetime.now().date():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave de acesso vencida."
        )

    user_perfil = getattr(user, "perfil", "gestor") or "gestor"

    # Validation: If perfil is 'faturamento', worker_key is MANDATORY on LOGIN!
    if user_perfil == "faturamento" and not (request.worker_key and request.worker_key.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o Código do Worker ativo para efetuar login com perfil Faturamento."
        )

    # Validate worker_key if provided or required
    if request.worker_key and request.worker_key.strip():
        w_key = request.worker_key.strip()
        uw = db.query(UserWorker).filter(
            UserWorker.worker_key == w_key,
            UserWorker.ativo == True
        ).first()
        
        if not uw and user_perfil in ["gestor", "admin"]:
            # Auto-register for gestor/admin if key exists or was generated
            target_user_id = user.parent_user_id if user.parent_user_id else user.id
            uw = UserWorker(user_id=target_user_id, worker_key=w_key, descricao="Registered at login", ativo=True)
            db.add(uw)
            db.commit()
        elif not uw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Código do Worker inválido ou worker não está ativo no momento."
            )

    payload = {
        "sub": str(user.id),
        "username": user.username,
        "is_admin": user.is_admin,
        "perfil": user_perfil,
        "worker_key": request.worker_key,
        "prefixo_identificacao": getattr(user, "prefixo_identificacao", None),
        "parent_user_id": user.parent_user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": datetime.now(timezone.utc)
    }
    jwt_token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "token": jwt_token,
        "username": user.username,
        "user_id": user.id,
        "perfil": user_perfil,
        "worker_key": request.worker_key,
        "prefixo_identificacao": getattr(user, "prefixo_identificacao", None),
        "parent_user_id": user.parent_user_id,
        "permissoes": getattr(user, "permissoes", None),
        "validade": user.validade.isoformat() if user.validade else None,
        "is_admin": user.is_admin
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem listar usuários.")
    
    users = db.query(User).filter(User.status == "Ativo").order_by(User.username).all()
    return [{"id": u.id, "username": u.username} for u in users]


class CreateUserRequest(BaseModel):
    username: str
    validade: Optional[str] = None  # YYYY-MM-DD
    is_admin: bool = False
    permitir_protocolo: bool = False
    status: str = "Ativo"
    integrador_ids: Optional[List[int]] = None

class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    validade: Optional[str] = None  # YYYY-MM-DD
    is_admin: Optional[bool] = None
    permitir_protocolo: Optional[bool] = None
    status: Optional[str] = None
    integrador_ids: Optional[List[int]] = None

@router.get("/admin/users")
def admin_list_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem gerenciar usuários.")
    
    users = db.query(User).order_by(User.username).all()
    
    # Fetch integrador_ids for each user
    user_ing_map = {}
    for ui in db.query(UserIntegrador).all():
        if ui.user_id not in user_ing_map:
            user_ing_map[ui.user_id] = []
        user_ing_map[ui.user_id].append(ui.id_integrador)
    
    return [
        {
            "id": u.id,
            "username": u.username,
            "api_key_masked": f"{u.api_key[:8]}..." if u.api_key else "",
            "validade": u.validade.isoformat() if u.validade else None,
            "status": u.status,
            "is_admin": u.is_admin,
            "permitir_protocolo": u.permitir_protocolo,
            "integrador_ids": user_ing_map.get(u.id, []),
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

    # Save integrador_ids
    if request.integrador_ids is not None:
        db.query(UserIntegrador).filter(UserIntegrador.user_id == user.id).delete()
        for ing_id in request.integrador_ids:
            db.add(UserIntegrador(user_id=user.id, id_integrador=ing_id, ativo=True))
        db.commit()
    
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "validade": user.validade.isoformat() if user.validade else None,
            "status": user.status,
            "is_admin": user.is_admin,
            "permitir_protocolo": user.permitir_protocolo,
            "integrador_ids": request.integrador_ids or []
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
                
    if request.integrador_ids is not None:
        db.query(UserIntegrador).filter(UserIntegrador.user_id == user.id).delete()
        for ing_id in request.integrador_ids:
            db.add(UserIntegrador(user_id=user.id, id_integrador=ing_id, ativo=True))

    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "username": user.username,
        "status": user.status,
        "is_admin": user.is_admin,
        "permitir_protocolo": user.permitir_protocolo,
        "validade": user.validade.isoformat() if user.validade else None,
        "integrador_ids": request.integrador_ids if request.integrador_ids is not None else [ui.id_integrador for ui in db.query(UserIntegrador).filter(UserIntegrador.user_id == user.id).all()]
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
        
    new_api_key = secrets.token_urlsafe(32)
    user.api_key = new_api_key
    
    db.commit()
    
    return {
        "message": "Nova chave de acesso gerada com sucesso.",
        "api_key": new_api_key
    }


def _resolve_and_sync_user_convenios(db: Session, target_user_id: int, parent_id: int, item_ids: List[int]):
    """
    Associa sub-usuário aos convênios.
    Suporta tanto user_convenios.id quanto convenios.id_convenio sem estourar Foreign Key error.
    """
    valid_uc_ids = []
    for item_id in (item_ids or []):
        # 1. Direct UserConvenio ID check
        uc = db.query(UserConvenio).filter(UserConvenio.id == item_id).first()
        if not uc:
            # 2. Check if item_id is an id_convenio for parent_id
            uc = db.query(UserConvenio).filter(
                UserConvenio.id_convenio == item_id,
                UserConvenio.user_id == parent_id
            ).first()
        if not uc:
            # 3. Create a UserConvenio entry for parent_id and item_id
            uc = UserConvenio(user_id=parent_id, id_convenio=item_id)
            db.add(uc)
            db.flush()
        
        if uc and uc.id not in valid_uc_ids:
            valid_uc_ids.append(uc.id)

    db.query(UserUserConvenio).filter(UserUserConvenio.user_id == target_user_id).delete()
    for uc_id in valid_uc_ids:
        db.add(UserUserConvenio(user_id=target_user_id, user_convenio_id=uc_id))


class CreateClientUserRequest(BaseModel):
    username: str
    login: str
    senha: str
    perfil: str  # supervisor, faturamento, agendamento
    prefixo_identificacao: Optional[str] = None  # R1, R2, A1, O1...
    worker_key: Optional[str] = None
    permissoes: Optional[dict] = None
    user_convenio_ids: List[int] = []

@router.get("/client-users")
def list_client_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Lista sub-usuários pertencentes ao gestor logado (parent_user_id = current_user.id)."""
    if current_user.is_admin:
        sub_users = db.query(User).filter(User.parent_user_id.isnot(None)).order_by(User.username).all()
    else:
        sub_users = db.query(User).filter(User.parent_user_id == current_user.id).order_by(User.username).all()

    result = []
    for u in sub_users:
        uucs = db.query(UserUserConvenio).filter(UserUserConvenio.user_id == u.id).all()
        conv_ids = []
        for x in uucs:
            uc = db.query(UserConvenio).filter(UserConvenio.id == x.user_convenio_id).first()
            if uc and uc.id_convenio:
                conv_ids.append(uc.id_convenio)
            else:
                conv_ids.append(x.user_convenio_id)

        workers = db.query(UserWorker).filter(UserWorker.user_id == u.id).all()
        result.append({
            "id": u.id,
            "username": u.username,
            "login": u.login,
            "perfil": u.perfil,
            "prefixo_identificacao": u.prefixo_identificacao,
            "status": u.status,
            "parent_user_id": u.parent_user_id,
            "user_convenio_ids": conv_ids,
            "permissoes": u.permissoes,
            "worker_keys": [w.worker_key for w in workers if w.ativo],
            "created_at": u.created_at.isoformat() if u.created_at else None
        })
    return result

@router.post("/client-users")
def create_client_user(request: CreateClientUserRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Cria um sub-usuário vinculado ao gestor logado (worker_key é facultativo no cadastro)."""
    try:
        user_perfil = getattr(current_user, "perfil", "gestor") or "gestor"
        if not current_user.is_admin and user_perfil != "gestor":
            raise HTTPException(status_code=403, detail="Apenas gestores podem criar sub-usuários.")

        existing = db.query(User).filter(User.login == request.login.strip()).first()
        if existing:
            raise HTTPException(status_code=400, detail="Este login já está em uso por outro usuário.")

        pw_hash = bcrypt.hashpw(request.senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        api_key = secrets.token_urlsafe(32)

        new_user = User(
            username=request.username.strip(),
            login=request.login.strip(),
            senha_hash=pw_hash,
            perfil=request.perfil,
            prefixo_identificacao=request.prefixo_identificacao.strip().upper() if request.prefixo_identificacao and request.prefixo_identificacao.strip() else None,
            parent_user_id=current_user.id,
            api_key=api_key,
            permissoes=request.permissoes,
            status="Ativo"
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        # Sync convênios
        if request.user_convenio_ids:
            _resolve_and_sync_user_convenios(db, new_user.id, current_user.id, request.user_convenio_ids)

        # Link worker_key if provided (facultativo)
        if request.worker_key and request.worker_key.strip():
            w_key = request.worker_key.strip()
            uw = db.query(UserWorker).filter(UserWorker.worker_key == w_key).first()
            if not uw:
                uw = UserWorker(user_id=new_user.id, worker_key=w_key, descricao=f"Worker key for {new_user.username}", ativo=True)
                db.add(uw)
            else:
                uw.user_id = new_user.id
                uw.ativo = True

        db.commit()
        return {
            "message": "Sub-usuário criado com sucesso.",
            "user_id": new_user.id,
            "login": new_user.login,
            "perfil": new_user.perfil,
            "prefixo_identificacao": new_user.prefixo_identificacao
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar sub-usuário: {str(e)}")


class UpdateClientUserRequest(BaseModel):
    username: Optional[str] = None
    login: Optional[str] = None
    senha: Optional[str] = None
    perfil: Optional[str] = None
    prefixo_identificacao: Optional[str] = None
    worker_key: Optional[str] = None
    status: Optional[str] = None
    permissoes: Optional[dict] = None
    user_convenio_ids: Optional[List[int]] = None

@router.put("/client-users/{user_id}")
def update_client_user(
    user_id: int,
    request: UpdateClientUserRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Atualiza dados de um sub-usuário (Gestor ou Admin)."""
    try:
        user_perfil = getattr(current_user, "perfil", "gestor") or "gestor"
        if not current_user.is_admin and user_perfil != "gestor":
            raise HTTPException(status_code=403, detail="Apenas gestores podem editar sub-usuários.")

        sub_user = db.query(User).filter(User.id == user_id).first()
        if not sub_user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")

        if not current_user.is_admin and sub_user.parent_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Você não tem permissão para editar este usuário.")

        if request.username and request.username.strip():
            sub_user.username = request.username.strip()

        if request.login and request.login.strip() != sub_user.login:
            existing = db.query(User).filter(User.login == request.login.strip(), User.id != user_id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Este login já está em uso por outro usuário.")
            sub_user.login = request.login.strip()

        if request.senha and request.senha.strip():
            pw_hash = bcrypt.hashpw(request.senha.strip().encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            sub_user.senha_hash = pw_hash

        if request.perfil:
            sub_user.perfil = request.perfil

        if request.prefixo_identificacao is not None:
            sub_user.prefixo_identificacao = request.prefixo_identificacao.strip().upper() if request.prefixo_identificacao and request.prefixo_identificacao.strip() else None

        if request.status:
            sub_user.status = request.status

        if request.permissoes is not None:
            sub_user.permissoes = request.permissoes

        # Sync user_convenios safely
        if request.user_convenio_ids is not None:
            parent_id = sub_user.parent_user_id or current_user.id
            _resolve_and_sync_user_convenios(db, sub_user.id, parent_id, request.user_convenio_ids)

        # Worker key (optional)
        if request.worker_key and request.worker_key.strip():
            w_key = request.worker_key.strip()
            uw = db.query(UserWorker).filter(UserWorker.worker_key == w_key).first()
            if not uw:
                uw = UserWorker(user_id=sub_user.id, worker_key=w_key, descricao=f"Worker key for {sub_user.username}", ativo=True)
                db.add(uw)
            else:
                uw.user_id = sub_user.id
                uw.ativo = True

        db.commit()
        return {"message": "Sub-usuário atualizado com sucesso.", "user_id": sub_user.id}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar usuário: {str(e)}")


@router.patch("/client-users/{user_id}/status")
def toggle_client_user_status(
    user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Ativa ou inativa o sub-usuário (Gestor ou Admin)."""
    user_perfil = getattr(current_user, "perfil", "gestor") or "gestor"
    if not current_user.is_admin and user_perfil != "gestor":
        raise HTTPException(status_code=403, detail="Apenas gestores podem alterar status de sub-usuários.")

    sub_user = db.query(User).filter(User.id == user_id).first()
    if not sub_user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    if not current_user.is_admin and sub_user.parent_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Você não tem permissão para alterar este usuário.")

    new_status = "Inativo" if sub_user.status == "Ativo" else "Ativo"
    sub_user.status = new_status
    db.commit()

    return {"message": f"Usuário {new_status.lower()} com sucesso.", "user_id": sub_user.id, "status": new_status}


