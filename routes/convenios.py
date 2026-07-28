from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import Convenio, UserConvenio, User
from pydantic import BaseModel
from typing import List, Optional
from dependencies import get_current_user
from security_utils import encrypt_password

router = APIRouter(
    prefix="/convenios",
    tags=["convenios"]
)

class ConvenioBase(BaseModel):
    nome: str

class ConvenioCreate(ConvenioBase):
    pass

class ConvenioUpdate(BaseModel):
    nome: Optional[str] = None
    registro_ans: Optional[str] = None

from pydantic import Field

class ConvenioOperacaoResponse(BaseModel):
    id: int
    descricao: str
    valor: str
    
    class Config:
        from_attributes = True

class ConvenioResponse(ConvenioBase):
    id_convenio: int
    registro_ans: Optional[str] = None
    operacoes: List[ConvenioOperacaoResponse] = Field(default=[], validation_alias="operacoes_rel")
    
    class Config:
        from_attributes = True
        populate_by_name = True

@router.get("/active-in-range")
def list_convenios_active_in_range(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Retorna apenas os convênios que possuem agendamentos no período de datas selecionado.
    """
    from models import Agendamento, Convenio
    from datetime import datetime

    query = db.query(Agendamento.id_convenio, Agendamento.nome_convenio).distinct()
    if not current_user.is_admin:
        query = query.filter(Agendamento.user_id == current_user.id)

    if data_inicio:
        try:
            d_ini = datetime.strptime(data_inicio[:10], "%Y-%m-%d").date()
            query = query.filter(Agendamento.data >= d_ini)
        except Exception: pass

    if data_fim:
        try:
            d_fim = datetime.strptime(data_fim[:10], "%Y-%m-%d").date()
            query = query.filter(Agendamento.data <= d_fim)
        except Exception: pass

    results = query.all()
    convs = [{"id_convenio": cid, "nome": cnome or f"Convênio #{cid}"} for cid, cnome in results if cid]
    if not convs:
        all_c = db.query(Convenio).all()
        return [{"id_convenio": c.id_convenio, "nome": c.nome} for c in all_c]
    return convs

@router.get("", response_model=List[ConvenioResponse])
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
    if conv.registro_ans is not None: db_conv.registro_ans = conv.registro_ans
    
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


@router.get("/all", response_model=List[ConvenioResponse])
def list_all_convenios(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Retorna todos os convênios cadastrados no sistema (sem filtro por usuário)."""
    from sqlalchemy.orm import joinedload
    return db.query(Convenio).options(joinedload(Convenio.operacoes_rel)).all()


@router.get("/worker-convenios")
def list_worker_convenios(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Lista convênios do schema worker."""
    from models import WorkerConvenio
    w_convs = db.query(WorkerConvenio).filter(WorkerConvenio.ativo == True).all()
    return [
        {
            "id_convenio": c.id_convenio,
            "nome": c.nome,
            "sigla": c.sigla,
            "ativo": c.ativo
        }
        for c in w_convs
    ]


class UserConvenioAssignRequest(BaseModel):
    user_id: int
    id_convenio: int
    worker_id_convenio: Optional[int] = None
    auto_confirmar: Optional[bool] = False
    auto_executar: Optional[bool] = False
    auto_faturar: Optional[bool] = False


@router.get("/user-assignments")
def list_user_assignments(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    uconvs = db.query(UserConvenio).all()
    users = {u.id: u.username for u in db.query(User).all()}
    convs = {c.id_convenio: c.nome for c in db.query(Convenio).all()}
    
    from models import WorkerConvenio
    w_convs = {c.id_convenio: c.nome for c in db.query(WorkerConvenio).all()}
    
    res = []
    for uc in uconvs:
        # Resolve worker_id_convenio automatically if match exists in worker schema
        effective_worker_id = uc.worker_id_convenio or (uc.id_convenio if uc.id_convenio in w_convs else None)
        worker_nome = w_convs.get(effective_worker_id)
        
        res.append({
            "id": uc.id,
            "user_id": uc.user_id,
            "username": users.get(uc.user_id, "Desconhecido"),
            "id_convenio": uc.id_convenio,
            "nome_convenio": convs.get(uc.id_convenio, "Desconhecido"),
            "worker_id_convenio": effective_worker_id,
            "nome_worker_convenio": worker_nome,
            "has_automacao": bool(worker_nome),
            "auto_confirmar": bool(uc.auto_confirmar),
            "auto_executar": bool(uc.auto_executar),
            "auto_faturar": bool(uc.auto_faturar)
        })
    return res


@router.post("/user-assignments")
def create_user_assignment(req: UserConvenioAssignRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    existing = db.query(UserConvenio).filter(
        UserConvenio.user_id == req.user_id,
        UserConvenio.id_convenio == req.id_convenio
    ).first()
    
    if existing:
        if req.worker_id_convenio is not None:
            existing.worker_id_convenio = req.worker_id_convenio
        if req.auto_confirmar is not None:
            existing.auto_confirmar = req.auto_confirmar
        if req.auto_executar is not None:
            existing.auto_executar = req.auto_executar
        if req.auto_faturar is not None:
            existing.auto_faturar = req.auto_faturar
        db.commit()
        return {"message": "Atribuição e workflow atualizados com sucesso.", "id": existing.id}
    
    new_uc = UserConvenio(
        user_id=req.user_id,
        id_convenio=req.id_convenio,
        worker_id_convenio=req.worker_id_convenio,
        auto_confirmar=req.auto_confirmar or False,
        auto_executar=req.auto_executar or False,
        auto_faturar=req.auto_faturar or False
    )
    db.add(new_uc)
    db.commit()
    db.refresh(new_uc)
    return {"message": "Convênio atribuído ao usuário com sucesso.", "id": new_uc.id}


@router.delete("/user-assignments/{id}")
def delete_user_assignment(id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    uc = db.query(UserConvenio).filter(UserConvenio.id == id).first()
    if not uc:
        raise HTTPException(status_code=404, detail="Atribuição não encontrada.")
    
    db.delete(uc)
    db.commit()
    return {"message": "Atribuição removida com sucesso."}


class UpdateWorkflowPipelineRequest(BaseModel):
    auto_confirmar: Optional[bool] = None
    auto_executar: Optional[bool] = None
    auto_faturar: Optional[bool] = None

@router.put("/user-assignments/{id}/workflow")
def update_user_assignment_workflow(id: int, req: UpdateWorkflowPipelineRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    uc = db.query(UserConvenio).filter(UserConvenio.id == id).first()
    if not uc:
        raise HTTPException(status_code=404, detail="Atribuição de convênio não encontrada.")
    
    if req.auto_confirmar is not None:
        uc.auto_confirmar = req.auto_confirmar
    if req.auto_executar is not None:
        uc.auto_executar = req.auto_executar
    if req.auto_faturar is not None:
        uc.auto_faturar = req.auto_faturar
        
    db.commit()
    return {"message": "Configurações de automação do workflow atualizadas.", "id": uc.id}


@router.get("/worker-operacoes")
def list_worker_operacoes(id_convenio: Optional[int] = None, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Lista rotinas/operações de workflow do worker, opcionalmente filtradas estritamente por convênio."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    from models import WorkerConvenioOperacao, WorkerConvenio
    query = db.query(WorkerConvenioOperacao)
    if id_convenio is not None:
        query = query.filter(WorkerConvenioOperacao.id_convenio == id_convenio)

    ops = query.all()
    convs = {c.id_convenio: c.nome for c in db.query(WorkerConvenio).all()}
    
    return [
        {
            "id": op.id,
            "id_convenio": op.id_convenio,
            "nome_convenio": convs.get(op.id_convenio, f"Convênio {op.id_convenio}"),
            "rotina": op.rotina,
            "descricao": op.descricao,
            "ativo": op.ativo,
            "modo_execucao": op.modo_execucao or "automatico"
        }
        for op in ops
    ]


class UpdateOperacaoRequest(BaseModel):
    ativo: Optional[bool] = None
    modo_execucao: Optional[str] = None

@router.put("/worker-operacoes/{id}")
def update_worker_operacao(id: int, req: UpdateOperacaoRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Atualiza status (ativo/inativo) e modo de execução (automático/manual) de uma rotina no worker."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    from models import WorkerConvenioOperacao
    op = db.query(WorkerConvenioOperacao).filter(WorkerConvenioOperacao.id == id).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operação não encontrada.")
    
    if req.ativo is not None:
        op.ativo = req.ativo
    if req.modo_execucao is not None:
        op.modo_execucao = req.modo_execucao
        
    db.commit()
    return {
        "message": "Operação de workflow atualizada com sucesso.", 
        "id": op.id, 
        "ativo": op.ativo,
        "modo_execucao": op.modo_execucao
    }



