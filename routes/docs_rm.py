from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Header, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user, get_effective_user_id, JWT_SECRET, JWT_ALGORITHM
from models import RelatorioMedico, RelatorioMedicoExtracao, Carteirinha, User
from typing import Optional
from pydantic import BaseModel
from datetime import date, datetime
from services import storage_service
import uuid
import os
import requests
import jwt

router = APIRouter()

class RelatorioMedicoCreate(BaseModel):
    id_paciente: str
    nome_paciente: Optional[str] = None
    id_relatorio: Optional[str] = None
    url_arquivo: Optional[str] = None
    data_relatorio: Optional[date] = None
    nome_medico: Optional[str] = None
    especialidade: Optional[str] = None
    observacoes: Optional[str] = None
    is_active: Optional[bool] = True

class RelatorioMedicoUpdate(BaseModel):
    nome_paciente: Optional[str] = None
    url_arquivo: Optional[str] = None
    data_relatorio: Optional[date] = None
    nome_medico: Optional[str] = None
    especialidade: Optional[str] = None
    observacoes: Optional[str] = None
    is_active: Optional[bool] = None

class RelatorioMedicoDateUpdate(BaseModel):
    data_relatorio: date

def resolve_user_for_view(token: Optional[str], authorization: Optional[str], db: Session) -> User:
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split(" ")[1]
    elif token:
        raw_token = token
        
    if not raw_token:
        raise HTTPException(status_code=401, detail="Token de autenticação não fornecido.")
        
    try:
        payload = jwt.decode(raw_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.status == "Ativo":
            return user
    except Exception:
        pass
        
    user = db.query(User).filter(User.api_key == raw_token).first()
    if user and user.status == "Ativo":
        return user
        
    raise HTTPException(status_code=401, detail="Token inválido ou sessão expirada.")

@router.get("", response_model=None)
@router.get("/", include_in_schema=False)
def list_relatorios_medicos(
    search: Optional[str] = Query(None, description="Busca por nome do paciente ou médico"),
    data_inicio: Optional[date] = Query(None, description="Data início do filtro"),
    data_fim: Optional[date] = Query(None, description="Data fim do filtro"),
    is_active: Optional[bool] = Query(None, description="Filtro de status ativo/inativo"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioMedico)
    if not current_user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)

    if is_active is not None:
        query = query.filter(RelatorioMedico.is_active == is_active)

    if data_inicio:
        query = query.filter(RelatorioMedico.data_relatorio >= data_inicio)

    if data_fim:
        query = query.filter(RelatorioMedico.data_relatorio <= data_fim)

    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            (RelatorioMedico.nome_paciente.ilike(search_term)) |
            (RelatorioMedico.nome_medico.ilike(search_term)) |
            (RelatorioMedico.id_paciente.ilike(search_term))
        )

    total = query.count()
    items = query.order_by(RelatorioMedico.data_relatorio.desc().nullslast(), RelatorioMedico.id.desc()) \
                 .offset((page - 1) * page_size) \
                 .limit(page_size) \
                 .all()

    return {
        "items": [
            {
                "id": r.id,
                "id_paciente": r.id_paciente,
                "nome_paciente": r.nome_paciente,
                "id_relatorio": r.id_relatorio,
                "url_arquivo": r.url_arquivo,
                "data_relatorio": r.data_relatorio.isoformat() if r.data_relatorio else None,
                "nome_medico": r.nome_medico,
                "especialidade": r.especialidade,
                "observacoes": r.observacoes,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "has_extracao": len(r.extracoes) > 0 if r.id_relatorio else False
            }
            for r in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 1
    }

@router.get("/{id}/view")
def view_document_content_rm(
    id: int,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Proxy/Stream autenticado do documento PDF para visualização em iframe sem bloqueio de login."""
    user = resolve_user_for_view(token, authorization, db)
    target_uid = get_effective_user_id(user)

    query = db.query(RelatorioMedico).filter(RelatorioMedico.id == id)
    if not user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)
    doc = query.first()

    if not doc:
        raise HTTPException(status_code=404, detail="Relatório Médico não encontrado.")

    url = doc.url_arquivo
    if not url:
        raise HTTPException(status_code=404, detail="Documento sem URL de arquivo cadastrada.")

    # 1. Arquivo local (/uploads/...)
    if url.startswith("/uploads/") or os.path.exists(url):
        clean_path = url.lstrip("/")
        if os.path.exists(clean_path):
            with open(clean_path, "rb") as f:
                content = f.read()
            return Response(
                content=content,
                media_type="application/pdf",
                headers={"Content-Disposition": f"inline; filename=rm_{id}.pdf"}
            )

    # 2. Portal Evoluir (se aplicável)
    if "sistemaevoluir.com.br" in url:
        from routes.jobs import get_evoluir_session
        try:
            session = get_evoluir_session(db, target_uid)
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return Response(
                    content=r.content,
                    media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=rm_{id}.pdf"}
                )
            else:
                raise HTTPException(status_code=502, detail=f"Erro ao obter PDF do portal Evoluir ({r.status_code}).")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro de autenticação/conexão com Evoluir: {str(e)}")

    # 3. URL externa (Supabase Storage, etc.)
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            content_type = r.headers.get("Content-Type", "application/pdf")
            return Response(
                content=r.content,
                media_type=content_type,
                headers={"Content-Disposition": f"inline; filename=rm_{id}.pdf"}
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar arquivo externo: {str(e)}")

    return RedirectResponse(url=url)

@router.post("", response_model=None)
@router.post("/", include_in_schema=False)
def create_relatorio_medico(
    payload: RelatorioMedicoCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    nome_pac = payload.nome_paciente
    if not nome_pac:
        cart = db.query(Carteirinha).filter(
            Carteirinha.id_paciente == payload.id_paciente,
            Carteirinha.user_id == target_uid
        ).first()
        if cart:
            nome_pac = cart.paciente

    id_rel = payload.id_relatorio or str(uuid.uuid4())

    rm = RelatorioMedico(
        user_id=target_uid,
        id_paciente=payload.id_paciente,
        nome_paciente=nome_pac,
        id_relatorio=id_rel,
        url_arquivo=payload.url_arquivo,
        data_relatorio=payload.data_relatorio,
        nome_medico=payload.nome_medico,
        especialidade=payload.especialidade,
        observacoes=payload.observacoes,
        is_active=payload.is_active if payload.is_active is not None else True
    )
    db.add(rm)
    db.commit()
    db.refresh(rm)
    return {"status": "success", "id": rm.id, "id_relatorio": rm.id_relatorio}

@router.post("/upload")
def upload_relatorio_medico(
    file: UploadFile = File(...),
    id_paciente: str = Form(...),
    nome_paciente: Optional[str] = Form(None),
    data_relatorio: Optional[str] = Form(None),
    nome_medico: Optional[str] = Form(None),
    especialidade: Optional[str] = Form(None),
    observacoes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    content = file.file.read()
    filename = f"RM_{uuid.uuid4().hex[:8]}_{file.filename}"
    url_arquivo = storage_service.save_anexo(filename, content, content_type=file.content_type or "application/pdf")

    parsed_data = None
    if data_relatorio:
        try:
            parsed_data = datetime.strptime(data_relatorio, "%Y-%m-%d").date()
        except Exception:
            pass

    nome_pac = nome_paciente
    if not nome_pac:
        cart = db.query(Carteirinha).filter(
            Carteirinha.id_paciente == id_paciente,
            Carteirinha.user_id == target_uid
        ).first()
        if cart:
            nome_pac = cart.paciente

    id_rel = str(uuid.uuid4())

    rm = RelatorioMedico(
        user_id=target_uid,
        id_paciente=id_paciente,
        nome_paciente=nome_pac,
        id_relatorio=id_rel,
        url_arquivo=url_arquivo,
        data_relatorio=parsed_data,
        nome_medico=nome_medico,
        especialidade=especialidade,
        observacoes=observacoes,
        is_active=True
    )
    db.add(rm)
    db.commit()
    db.refresh(rm)
    return {"status": "success", "id": rm.id, "url_arquivo": url_arquivo}

@router.put("/{id}")
def update_relatorio_medico(
    id: int,
    payload: RelatorioMedicoUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioMedico).filter(RelatorioMedico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)
    rm = query.first()

    if not rm:
        raise HTTPException(status_code=404, detail="Relatório Médico não encontrado.")

    if payload.nome_paciente is not None:
        rm.nome_paciente = payload.nome_paciente
    if payload.url_arquivo is not None:
        rm.url_arquivo = payload.url_arquivo
    if payload.data_relatorio is not None:
        rm.data_relatorio = payload.data_relatorio
    if payload.nome_medico is not None:
        rm.nome_medico = payload.nome_medico
    if payload.especialidade is not None:
        rm.especialidade = payload.especialidade
    if payload.observacoes is not None:
        rm.observacoes = payload.observacoes
    if payload.is_active is not None:
        rm.is_active = payload.is_active

    db.commit()
    return {"status": "success", "id": rm.id}

@router.patch("/{id}/toggle")
def toggle_relatorio_medico(
    id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioMedico).filter(RelatorioMedico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)
    rm = query.first()

    if not rm:
        raise HTTPException(status_code=404, detail="Relatório Médico não encontrado.")

    rm.is_active = not rm.is_active
    db.commit()
    return {"status": "success", "id": rm.id, "is_active": rm.is_active}

@router.patch("/{id}/data")
def update_relatorio_medico_data(
    id: int,
    payload: RelatorioMedicoDateUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioMedico).filter(RelatorioMedico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)
    rm = query.first()

    if not rm:
        raise HTTPException(status_code=404, detail="Relatório Médico não encontrado.")

    rm.data_relatorio = payload.data_relatorio
    db.commit()
    return {"status": "success", "id": rm.id, "data_relatorio": rm.data_relatorio.isoformat()}

@router.delete("/{id}")
def delete_relatorio_medico(
    id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioMedico).filter(RelatorioMedico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)
    rm = query.first()

    if not rm:
        raise HTTPException(status_code=404, detail="Relatório Médico não encontrado.")

    db.delete(rm)
    db.commit()
    return {"status": "success", "id": id}

@router.get("/{id}/extracoes")
def get_relatorio_medico_extracoes(
    id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioMedico).filter(RelatorioMedico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioMedico.user_id == target_uid)
    rm = query.first()

    if not rm:
        raise HTTPException(status_code=404, detail="Relatório Médico não encontrado.")

    if not rm.id_relatorio:
        return {"items": []}

    extracoes = db.query(RelatorioMedicoExtracao).filter(
        RelatorioMedicoExtracao.id_relatorio == rm.id_relatorio
    ).all()

    return {
        "items": [
            {
                "id": ext.id,
                "id_relatorio": ext.id_relatorio,
                "status_extracao": ext.status_extracao,
                "carga_psicologia": ext.carga_psicologia,
                "carga_fisioterapia": ext.carga_fisioterapia,
                "carga_terapia_ocupacional": ext.carga_terapia_ocupacional,
                "carga_psicopedagogia": ext.carga_psicopedagogia,
                "carga_fonoaudiologia": ext.carga_fonoaudiologia,
                "carga_psicomotricidade": ext.carga_psicomotricidade,
                "carga_musicoterapia": ext.carga_musicoterapia,
                "carga_avaliacao_neuropsicologica": ext.carga_avaliacao_neuropsicologica,
                "tipo_carga_horaria": ext.tipo_carga_horaria,
                "created_at": ext.created_at.isoformat() if ext.created_at else None
            }
            for ext in extracoes
        ]
    }
