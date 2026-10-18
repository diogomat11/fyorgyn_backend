from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Header, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import cast, Text
from database import get_db
from dependencies import get_current_user, get_effective_user_id, JWT_SECRET, JWT_ALGORITHM
from models import RelatorioClinico, Carteirinha, Job, User
from typing import Optional
from pydantic import BaseModel
from datetime import date, datetime
from services import storage_service
from media_utils import infer_media_type, inline_filename
import uuid
import json
import os
import requests
import jwt

router = APIRouter()

class RelatorioClinicoCreate(BaseModel):
    id_paciente: str
    tipo_relatorio: str  # 'PTS' ou 'ANEXO-II'
    nome_paciente: Optional[str] = None
    id_relatorio: Optional[str] = None
    url_arquivo: Optional[str] = None
    carga: Optional[str] = None
    tipo_carga_horaria: Optional[str] = None
    id_area: Optional[int] = None
    data: Optional[date] = None
    nome_profissional: Optional[str] = None
    is_active: Optional[bool] = True

class RelatorioClinicoUpdate(BaseModel):
    nome_paciente: Optional[str] = None
    url_arquivo: Optional[str] = None
    carga: Optional[str] = None
    tipo_carga_horaria: Optional[str] = None
    data: Optional[date] = None
    nome_profissional: Optional[str] = None
    is_active: Optional[bool] = None

class RelatorioClinicoDateUpdate(BaseModel):
    data: date

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
def list_relatorios_clinicos(
    tipo_relatorio: str = Query(..., description="Tipo do relatório: PTS ou ANEXO-II"),
    search: Optional[str] = Query(None, description="Busca por nome do paciente ou profissional"),
    data_inicio: Optional[date] = Query(None, description="Data início do filtro"),
    data_fim: Optional[date] = Query(None, description="Data fim do filtro"),
    is_active: Optional[bool] = Query(None, description="Filtro de status ativo/inativo"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioClinico).filter(
        RelatorioClinico.tipo_relatorio == tipo_relatorio.upper()
    )

    if not current_user.is_admin:
        query = query.filter(RelatorioClinico.user_id == target_uid)

    if is_active is not None:
        query = query.filter(RelatorioClinico.is_active == is_active)

    if data_inicio:
        query = query.filter(RelatorioClinico.data >= data_inicio)

    if data_fim:
        query = query.filter(RelatorioClinico.data <= data_fim)

    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            (RelatorioClinico.nome_paciente.ilike(search_term)) |
            (RelatorioClinico.nome_profissional.ilike(search_term)) |
            (RelatorioClinico.id_paciente.ilike(search_term))
        )

    total = query.count()
    items = query.order_by(RelatorioClinico.data.desc().nullslast(), RelatorioClinico.id.desc()) \
                 .offset((page - 1) * page_size) \
                 .limit(page_size) \
                 .all()

    return {
        "items": [
            {
                "id": r.id,
                "id_paciente": r.id_paciente,
                "nome_paciente": r.nome_paciente,
                "tipo_relatorio": r.tipo_relatorio,
                "id_relatorio": r.id_relatorio,
                "url_arquivo": r.url_arquivo,
                "carga": r.carga,
                "tipo_carga_horaria": r.tipo_carga_horaria,
                "id_area": r.id_area,
                "data": r.data.isoformat() if r.data else None,
                "nome_profissional": r.nome_profissional,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 1
    }

@router.get("/{id}/view")
def view_document_content(
    id: int,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Proxy/Stream autenticado do documento PDF para visualização em iframe sem bloqueio de login."""
    user = resolve_user_for_view(token, authorization, db)
    target_uid = get_effective_user_id(user)

    query = db.query(RelatorioClinico).filter(RelatorioClinico.id == id)
    if not user.is_admin:
        query = query.filter(RelatorioClinico.user_id == target_uid)
    doc = query.first()

    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")

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
                media_type=infer_media_type(url),
                headers={"Content-Disposition": f"inline; filename={inline_filename('doc', id, url)}"}
            )

    # 2. Portal Evoluir (sistemaevoluir.com.br) - Baixa autenticado
    if "sistemaevoluir.com.br" in url:
        from routes.jobs import get_evoluir_session
        try:
            session = get_evoluir_session(db, target_uid)
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return Response(
                    content=r.content,
                    media_type=infer_media_type(url, upstream_content_type=r.headers.get("Content-Type")),
                    headers={"Content-Disposition": f"inline; filename={inline_filename('doc', id, url)}"}
                )
            else:
                raise HTTPException(status_code=502, detail=f"Erro ao obter PDF do portal Evoluir ({r.status_code}).")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro de autenticação/conexão com Evoluir: {str(e)}")

    # 3. URL externa (Supabase Storage, S3, etc.)
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            content_type = r.headers.get("Content-Type", "application/pdf")
            return Response(
                content=r.content,
                media_type=infer_media_type(url, upstream_content_type=content_type),
                headers={"Content-Disposition": f"inline; filename={inline_filename('doc', id, url)}"}
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar arquivo externo: {str(e)}")

    return RedirectResponse(url=url)

@router.post("", response_model=None)
@router.post("/", include_in_schema=False)
def create_relatorio_clinico(
    payload: RelatorioClinicoCreate,
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

    rc = RelatorioClinico(
        user_id=target_uid,
        id_paciente=payload.id_paciente,
        nome_paciente=nome_pac,
        tipo_relatorio=payload.tipo_relatorio.upper(),
        id_relatorio=id_rel,
        url_arquivo=payload.url_arquivo,
        carga=payload.carga,
        tipo_carga_horaria=payload.tipo_carga_horaria,
        id_area=payload.id_area,
        data=payload.data,
        nome_profissional=payload.nome_profissional,
        is_active=payload.is_active if payload.is_active is not None else True
    )
    db.add(rc)
    db.commit()
    db.refresh(rc)
    return {"status": "success", "id": rc.id, "id_relatorio": rc.id_relatorio}

@router.post("/upload")
def upload_relatorio_clinico(
    file: UploadFile = File(...),
    id_paciente: str = Form(...),
    tipo_relatorio: str = Form(...),  # 'PTS' ou 'ANEXO-II'
    nome_paciente: Optional[str] = Form(None),
    data: Optional[str] = Form(None),
    nome_profissional: Optional[str] = Form(None),
    carga: Optional[str] = Form(None),
    tipo_carga_horaria: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    tipo_norm = tipo_relatorio.upper()
    prefix = "PTS" if "PTS" in tipo_norm else "AI"
    content = file.file.read()
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}_{file.filename}"
    url_arquivo = storage_service.save_anexo(filename, content, content_type=file.content_type or "application/pdf")

    parsed_data = None
    if data:
        try:
            parsed_data = datetime.strptime(data, "%Y-%m-%d").date()
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

    rc = RelatorioClinico(
        user_id=target_uid,
        id_paciente=id_paciente,
        nome_paciente=nome_pac,
        tipo_relatorio=tipo_norm,
        id_relatorio=id_rel,
        url_arquivo=url_arquivo,
        carga=carga,
        tipo_carga_horaria=tipo_carga_horaria,
        data=parsed_data,
        nome_profissional=nome_profissional,
        is_active=True
    )
    db.add(rc)
    db.commit()
    db.refresh(rc)
    return {"status": "success", "id": rc.id, "url_arquivo": url_arquivo}

@router.put("/{id}")
def update_relatorio_clinico(
    id: int,
    payload: RelatorioClinicoUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioClinico).filter(RelatorioClinico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioClinico.user_id == target_uid)
    rc = query.first()

    if not rc:
        raise HTTPException(status_code=404, detail="Relatório Clínico não encontrado.")

    if payload.nome_paciente is not None:
        rc.nome_paciente = payload.nome_paciente
    if payload.url_arquivo is not None:
        rc.url_arquivo = payload.url_arquivo
    if payload.carga is not None:
        rc.carga = payload.carga
    if payload.tipo_carga_horaria is not None:
        rc.tipo_carga_horaria = payload.tipo_carga_horaria
    if payload.nome_profissional is not None:
        rc.nome_profissional = payload.nome_profissional
    if payload.is_active is not None:
        rc.is_active = payload.is_active

    job_enqueued = False
    if payload.data is not None:
        data_mudou = (rc.data != payload.data)
        rc.data = payload.data

        if data_mudou and rc.id_relatorio:
            formatted_date = payload.data.strftime("%d/%m/%Y")
            exists = db.query(Job.id).filter(
                Job.user_id == target_uid,
                Job.id_convenio == 100,
                Job.rotina.in_(['op4_atualizarDataPTS', 'op4', '4']),
                Job.status.in_(['pending', 'processing']),
                cast(Job.params, Text).ilike(f'%{rc.id_relatorio}%')
            ).first()

            if not exists:
                job_params = json.dumps({
                    'id_relatorio': rc.id_relatorio,
                    'data': formatted_date,
                    'id_paciente': rc.id_paciente,
                    'nome_paciente': rc.nome_paciente,
                    'tipo_relatorio': rc.tipo_relatorio
                })
                job = Job(
                    status='pending',
                    id_convenio=100,
                    user_id=target_uid,
                    rotina='op4_atualizarDataPTS',
                    params=job_params,
                    priority=0
                )
                db.add(job)
                job_enqueued = True

    db.commit()
    return {"status": "success", "id": rc.id, "job_enqueued": job_enqueued}

@router.patch("/{id}/toggle")
def toggle_relatorio_clinico(
    id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioClinico).filter(RelatorioClinico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioClinico.user_id == target_uid)
    rc = query.first()

    if not rc:
        raise HTTPException(status_code=404, detail="Relatório Clínico não encontrado.")

    rc.is_active = not rc.is_active
    db.commit()
    return {"status": "success", "id": rc.id, "is_active": rc.is_active}

@router.patch("/{id}/data")
def update_relatorio_clinico_data(
    id: int,
    payload: RelatorioClinicoDateUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioClinico).filter(RelatorioClinico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioClinico.user_id == target_uid)
    rc = query.first()

    if not rc:
        raise HTTPException(status_code=404, detail="Relatório Clínico não encontrado.")

    rc.data = payload.data

    # Cria job para atualizar data no portal Evoluir
    job_enqueued = False
    if rc.id_relatorio:
        formatted_date = payload.data.strftime("%d/%m/%Y")
        exists = db.query(Job.id).filter(
            Job.user_id == target_uid,
            Job.id_convenio == 100,
            Job.rotina.in_(['op4_atualizarDataPTS', 'op4', '4']),
            Job.status.in_(['pending', 'processing']),
            cast(Job.params, Text).ilike(f'%{rc.id_relatorio}%')
        ).first()

        if not exists:
            job_params = json.dumps({
                'id_relatorio': rc.id_relatorio,
                'data': formatted_date,
                'id_paciente': rc.id_paciente,
                'nome_paciente': rc.nome_paciente,
                'tipo_relatorio': rc.tipo_relatorio
            })
            job = Job(
                status='pending',
                id_convenio=100,
                user_id=target_uid,
                rotina='op4_atualizarDataPTS',
                params=job_params,
                priority=0
            )
            db.add(job)
            job_enqueued = True

    db.commit()
    return {
        "status": "success",
        "id": rc.id,
        "data": rc.data.isoformat(),
        "job_enqueued": job_enqueued,
        "message": "Data atualizada e job de sincronização enfileirado para o portal Evoluir." if job_enqueued else "Data atualizada com sucesso."
    }

@router.delete("/{id}")
def delete_relatorio_clinico(
    id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_uid = get_effective_user_id(current_user)
    query = db.query(RelatorioClinico).filter(RelatorioClinico.id == id)
    if not current_user.is_admin:
        query = query.filter(RelatorioClinico.user_id == target_uid)
    rc = query.first()

    if not rc:
        raise HTTPException(status_code=404, detail="Relatório Clínico não encontrado.")

    db.delete(rc)
    db.commit()
    return {"status": "success", "id": id}

@router.post("/importar")
def importar_relatorios_portal(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Enfileira jobs op3_ListarPTS para todos os pacientes do usuário que tenham id_paciente do Evoluir."""
    target_uid = get_effective_user_id(current_user)
    query = db.query(Carteirinha).filter(
        Carteirinha.id_paciente != None,
        Carteirinha.id_paciente != ""
    )
    if not current_user.is_admin:
        query = query.filter(Carteirinha.user_id == target_uid)

    carts = query.all()

    enqueued_count = 0
    for c in carts:
        if len(c.id_paciente) > 20 and '-' in c.id_paciente:
            exists = db.query(Job.id).filter(
                Job.user_id == target_uid,
                Job.id_convenio == 100,
                Job.rotina.in_(['op3_ListarPTS', 'op3', '3']),
                Job.status.in_(['pending', 'processing']),
                cast(Job.params, Text).ilike(f'%{c.id_paciente}%')
            ).first()

            if not exists:
                op3_params = json.dumps({
                    'id_paciente': c.id_paciente,
                    'nome_paciente': c.paciente
                })
                j = Job(
                    status='pending',
                    id_convenio=100,
                    user_id=target_uid,
                    rotina='op3_ListarPTS',
                    params=op3_params,
                    priority=0
                )
                db.add(j)
                enqueued_count += 1

    db.commit()
    return {
        "status": "success",
        "message": f"{enqueued_count} jobs de importação enfileirados para os servidores de agendamento.",
        "enqueued_count": enqueued_count
    }
