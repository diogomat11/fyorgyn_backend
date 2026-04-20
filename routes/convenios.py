from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import Convenio
from pydantic import BaseModel
from typing import List, Optional
from dependencies import get_current_user
from security_utils import encrypt_password

router = APIRouter()

class ConvenioBase(BaseModel):
    nome: str
    usuario: Optional[str] = None

class ConvenioCreate(ConvenioBase):
    senha: Optional[str] = None

class ConvenioUpdate(BaseModel):
    nome: Optional[str] = None
    usuario: Optional[str] = None
    senha: Optional[str] = None

from pydantic import Field

class ConvenioOperacaoResponse(BaseModel):
    id: int
    descricao: str
    valor: str
    
    class Config:
        from_attributes = True

class ConvenioResponse(ConvenioBase):
    id_convenio: int
    codigo_referenciado: Optional[str] = None
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
    new_conv = Convenio(nome=conv.nome, usuario=conv.usuario)
    if conv.senha:
        new_conv.senha_criptografada = encrypt_password(conv.senha)
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
    if conv.usuario: db_conv.usuario = conv.usuario
    if conv.senha: db_conv.senha_criptografada = encrypt_password(conv.senha)
    
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

