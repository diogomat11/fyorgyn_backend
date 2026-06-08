from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import Convenio, UserConvenio, User
from pydantic import BaseModel
from typing import List, Optional
from dependencies import get_current_user
from security_utils import encrypt_password

router = APIRouter()

class ConvenioBase(BaseModel):
    nome: str

class ConvenioCreate(ConvenioBase):
    pass

class ConvenioUpdate(BaseModel):
    nome: Optional[str] = None

from pydantic import Field

class ConvenioOperacaoResponse(BaseModel):
    id: int
    descricao: str
    valor: str
    
    class Config:
        from_attributes = True

class ConvenioResponse(ConvenioBase):
    id_convenio: int
    operacoes: List[ConvenioOperacaoResponse] = Field(default=[], validation_alias="operacoes_rel")
    
    class Config:
        from_attributes = True
        populate_by_name = True

@router.get("/", response_model=List[ConvenioResponse])
def list_convenios(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    from sqlalchemy.orm import joinedload
    # Se o usuário tiver vínculos específicos na tabela user_convenios, retorna apenas esses
    if current_user.convenio_rel:
        allowed_ids = [c.id_convenio for c in current_user.convenio_rel]
        return db.query(Convenio).options(joinedload(Convenio.operacoes_rel)).filter(Convenio.id_convenio.in_(allowed_ids)).all()
    # Fallback legacy: se tiver um id_convenio setado diretamente, retorna só ele
    if current_user.id_convenio:
        return db.query(Convenio).options(joinedload(Convenio.operacoes_rel)).filter(Convenio.id_convenio == current_user.id_convenio).all()
    # Se não tiver nada (Admin), retorna todos
    return db.query(Convenio).options(joinedload(Convenio.operacoes_rel)).all()

@router.post("/", response_model=ConvenioResponse)
def create_convenio(conv: ConvenioCreate, db: Session = Depends(get_db)):
    new_conv = Convenio(nome=conv.nome)
    db.add(new_conv)
    db.commit()
    db.refresh(new_conv)
    return new_conv

@router.patch("/{id_convenio}", response_model=ConvenioResponse)
def update_convenio(id_convenio: int, conv: ConvenioUpdate, db: Session = Depends(get_db)):
    db_conv = db.query(Convenio).filter(Convenio.id_convenio == id_convenio).first()
    if not db_conv:
        raise HTTPException(status_code=404, detail="Convenio not found")
    
    if conv.nome: db_conv.nome = conv.nome
    
    db.commit()
    db.refresh(db_conv)
    return db_conv


@router.get("/{id_convenio}/procedimentos")
def list_procedimentos_by_convenio(id_convenio: int, db: Session = Depends(get_db)):
    """Retorna procedimentos de autorização do convênio para selects pesquisáveis."""
    from models import Procedimento
    procs = db.query(Procedimento).filter(
        Procedimento.id_convenio == id_convenio,
        Procedimento.status == "ativo"
    ).order_by(Procedimento.nome).all()
    return [
        {
            "id": p.id_procedimento,
            "codigo": p.codigo_procedimento,
            "nome": p.nome,
            "faturamento": p.faturamento
        }
        for p in procs
    ]


class CredentialCreateRequest(BaseModel):
    user_id: int
    id_convenio: int
    login: Optional[str] = None
    senha: Optional[str] = None
    cod_prestador: Optional[str] = None
    login_fat: Optional[str] = None
    senha_fat: Optional[str] = None
    url_portal_fat: Optional[str] = None

class CredentialUpdateRequest(BaseModel):
    login: Optional[str] = None
    senha: Optional[str] = None
    cod_prestador: Optional[str] = None
    login_fat: Optional[str] = None
    senha_fat: Optional[str] = None
    url_portal_fat: Optional[str] = None

@router.get("/credentials")
def list_credentials(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Apenas administradores podem acessar credenciais de convênios."
        )
    
    uconvs = db.query(UserConvenio).all()
    
    # Resolve usernames and convenio names
    users = {u.id: u.username for u in db.query(User).all()}
    convs = {c.id_convenio: c.nome for c in db.query(Convenio).all()}
    
    res = []
    for uc in uconvs:
        res.append({
            "id": uc.id,
            "user_id": uc.user_id,
            "username": users.get(uc.user_id, "Desconhecido"),
            "id_convenio": uc.id_convenio,
            "nome_convenio": convs.get(uc.id_convenio, "Desconhecido"),
            "login": uc.login,
            "has_senha": bool(uc.senha_criptografada),
            "cod_prestador": uc.cod_prestador,
            "login_fat": uc.login_fat,
            "has_senha_fat": bool(uc.senha_fat_criptografada),
            "url_portal_fat": uc.url_portal_fat
        })
    return res

@router.post("/credentials")
def create_credential(request: CredentialCreateRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Apenas administradores podem gerenciar credenciais."
        )
    
    # Encrypt passwords if provided
    senha_enc = encrypt_password(request.senha) if request.senha else None
    senha_fat_enc = encrypt_password(request.senha_fat) if request.senha_fat else None
    
    new_uc = UserConvenio(
        user_id=request.user_id,
        id_convenio=request.id_convenio,
        login=request.login,
        senha_criptografada=senha_enc,
        cod_prestador=request.cod_prestador,
        login_fat=request.login_fat,
        senha_fat_criptografada=senha_fat_enc,
        url_portal_fat=request.url_portal_fat
    )
    db.add(new_uc)
    db.commit()
    db.refresh(new_uc)
    return {"message": "Credenciais criadas com sucesso.", "id": new_uc.id}

@router.put("/credentials/{id}")
def update_credential(id: int, request: CredentialUpdateRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Apenas administradores podem gerenciar credenciais."
        )
    
    uc = db.query(UserConvenio).filter(UserConvenio.id == id).first()
    if not uc:
        raise HTTPException(status_code=404, detail="Credenciais não encontradas.")
    
    if request.login is not None:
        uc.login = request.login
    if request.senha: # Only update if not empty
        uc.senha_criptografada = encrypt_password(request.senha)
    if request.cod_prestador is not None:
        uc.cod_prestador = request.cod_prestador
    if request.login_fat is not None:
        uc.login_fat = request.login_fat
    if request.senha_fat: # Only update if not empty
        uc.senha_fat_criptografada = encrypt_password(request.senha_fat)
    if request.url_portal_fat is not None:
        uc.url_portal_fat = request.url_portal_fat
        
    db.commit()
    return {"message": "Credenciais atualizadas com sucesso."}

@router.delete("/credentials/{id}")
def delete_credential(id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Apenas administradores podem gerenciar credenciais."
        )
    
    uc = db.query(UserConvenio).filter(UserConvenio.id == id).first()
    if not uc:
        raise HTTPException(status_code=404, detail="Credenciais não encontradas.")
        
    db.delete(uc)
    db.commit()
    return {"message": "Credenciais removidas com sucesso."}

