"""
Routes for the Protocolo-Fichas module (PDF extraction via Gemini AI).

Endpoints:
    POST   /api/protocolo/lotes                    — Upload PDFs & create batch
    GET    /api/protocolo/lotes                    — List user's batches
    GET    /api/protocolo/lotes/{id}/status         — Detailed batch status + files
    POST   /api/protocolo/lotes/{id}/cancelar      — Cancel processing
    POST   /api/protocolo/lotes/{id}/reprocessar   — Reprocess failed files
    GET    /api/protocolo/arquivos/{id}/download    — Download individual file
    PATCH  /api/protocolo/arquivos/{id}             — Edit file's final name
    GET    /api/protocolo/lotes/{id}/download-zip   — Download ZIP (partitioned)
    GET    /api/protocolo/config                    — API keys/model status
"""

import os
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Form
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_protocolo_user

router = APIRouter(
    prefix="/protocolo",
    tags=["Protocolo Fichas"],
)


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class UpdateNomeRequest(BaseModel):
    nome_final: str

class AtendimentoItem(BaseModel):
    data: str
    assinatura: str

class UpdateAtendimentosRequest(BaseModel):
    atendimentos: List[AtendimentoItem]


# ---------------------------------------------------------------------------
# POST /lotes — Upload & Create Batch
# ---------------------------------------------------------------------------

@router.post("/lotes")
async def create_lote(
    files: List[UploadFile] = File(...),
    convenio: str = Form("unimed_goiania"),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """
    Upload multiple PDF files and create a processing batch.
    Returns the lote_id immediately; processing happens in background.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado")

    # Validate all files are PDFs
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Arquivo '{f.filename}' não é um PDF"
            )

    from services.protocolo_service import create_lote as svc_create

    try:
        result = svc_create(db, current_user.id, files, convenio)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar lote: {str(e)}")


# ---------------------------------------------------------------------------
# GET /lotes — List Batches
# ---------------------------------------------------------------------------

@router.get("/lotes")
def list_lotes(
    limit: int = Query(25, ge=1, le=100),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """List all batches for the current user."""
    from services.protocolo_service import list_lotes as svc_list

    return svc_list(db, user_id=current_user.id, limit=limit, skip=skip)


# ---------------------------------------------------------------------------
# GET /lotes/{id}/status — Detailed Status
# ---------------------------------------------------------------------------

@router.get("/lotes/{lote_id}/status")
def get_lote_status(
    lote_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Get detailed status of a lote and its files."""
    from services.protocolo_service import get_lote_status as svc_status
    from services.protocolo_service import recalculate_lote_totals
    from models import ProtocoloLote
    
    lote_basic = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote_basic:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    if not current_user.is_admin and lote_basic.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")
    
    # Recalculate only if still active (processing or pending)
    if lote_basic.status in ["pending", "processing"]:
        recalculate_lote_totals(db, lote_id)
    
    result = svc_status(db, lote_id)
    return result


# ---------------------------------------------------------------------------
# POST /lotes/{id}/cancelar — Cancel Processing
# ---------------------------------------------------------------------------

@router.post("/lotes/{lote_id}/cancelar")
def cancel_lote(
    lote_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Cancel an ongoing batch processing."""
    from services.protocolo_service import cancel_lote as svc_cancel
    from models import ProtocoloLote

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    success = svc_cancel(db, lote_id)
    if not success:
        raise HTTPException(status_code=400, detail="Não foi possível cancelar o lote (já finalizado ou inexistente)")

    return {"message": "Processamento cancelado com sucesso"}


# ---------------------------------------------------------------------------
# POST /lotes/{id}/reprocessar — Reprocess Errors
# ---------------------------------------------------------------------------

@router.post("/lotes/{lote_id}/reprocessar")
def reprocess_errors(
    lote_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Reprocess only the files that failed or need review."""
    from services.protocolo_service import reprocess_errors as svc_reprocess
    from models import ProtocoloLote

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    count = svc_reprocess(db, lote_id)
    if count == 0:
        raise HTTPException(status_code=400, detail="Nenhum arquivo com erro para reprocessar")

    return {"message": f"{count} arquivo(s) reenviado(s) para reprocessamento", "count": count}


# ---------------------------------------------------------------------------
# GET /arquivos/{id}/download — Individual Download
# ---------------------------------------------------------------------------

@router.get("/arquivos/{arquivo_id}/download")
def download_arquivo(
    arquivo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Download a single processed file."""
    from services.protocolo_service import get_arquivo_file_path
    from models import ProtocoloArquivo, ProtocoloLote

    arq = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arq:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arq.lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote associado não encontrado")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este arquivo.")

    result = get_arquivo_file_path(db, arquivo_id)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    filepath, filename = result
    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# PATCH /arquivos/{id} — Edit Final Filename
# ---------------------------------------------------------------------------

@router.patch("/arquivos/{arquivo_id}")
def update_arquivo(
    arquivo_id: int,
    body: UpdateNomeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Update the final filename of a file (manual override)."""
    from services.protocolo_service import update_arquivo_nome
    from models import ProtocoloArquivo, ProtocoloLote

    arq = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arq:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arq.lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote associado não encontrado")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este arquivo.")

    result = update_arquivo_nome(db, arquivo_id, body.nome_final)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return result


# ---------------------------------------------------------------------------
# PATCH /arquivos/{id}/atendimentos — Edit Datas/Assinaturas
# ---------------------------------------------------------------------------

@router.patch("/arquivos/{arquivo_id}/atendimentos")
def update_atendimentos(
    arquivo_id: int,
    body: UpdateAtendimentosRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Update the atendimentos (dates/signatures) of a file."""
    from services.protocolo_service import update_arquivo_atendimentos
    from models import ProtocoloArquivo, ProtocoloLote

    arq = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arq:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arq.lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote associado não encontrado")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este arquivo.")

    atend_dicts = [{"data": a.data, "assinatura": a.assinatura} for a in body.atendimentos]
    result = update_arquivo_atendimentos(db, arquivo_id, atend_dicts)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return result


# ---------------------------------------------------------------------------
# DELETE /arquivos/{id} — Delete Arquivo
# ---------------------------------------------------------------------------

@router.delete("/arquivos/{arquivo_id}")
def delete_arquivo(
    arquivo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Delete a single file from the session."""
    from services.protocolo_service import delete_arquivo as svc_delete
    from models import ProtocoloArquivo, ProtocoloLote

    arq = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arq:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arq.lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote associado não encontrado")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este arquivo.")

    result = svc_delete(db, arquivo_id)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return {"message": "Arquivo excluído com sucesso"}


# ---------------------------------------------------------------------------
# GET /lotes/{id}/download-zip — ZIP Download (Partitioned)
# ---------------------------------------------------------------------------

@router.get("/lotes/{lote_id}/download-zip")
def download_zip(
    lote_id: int,
    part: int = Query(1, ge=1, description="Part number (1-indexed)"),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """
    Download a ZIP file containing all successfully processed files.
    ZIPs are split into 10MB parts. Use ?part=N to download specific parts.
    """
    from services.protocolo_service import generate_download_zip
    from models import ProtocoloLote

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    zip_parts = generate_download_zip(db, lote_id)

    if not zip_parts:
        raise HTTPException(status_code=404, detail="Nenhum arquivo processado disponível para download")

    if part > len(zip_parts):
        raise HTTPException(
            status_code=400,
            detail=f"Part {part} não existe. Total de parts: {len(zip_parts)}"
        )

    zip_buffer = zip_parts[part - 1]
    part_suffix = f"_{part:02d}" if len(zip_parts) > 1 else ""
    filename = f"LOTE_{lote_id:03d}{part_suffix}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Parts": str(len(zip_parts)),
        },
    )


# ---------------------------------------------------------------------------
# GET /stats — Monthly Statistics
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Get monthly statistics (total files processed and total cost)."""
    from models import ProtocoloLote
    from sqlalchemy import func
    from datetime import datetime

    # Get current month start
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    # Calculate total successful files this month for this user
    stats = db.query(
        func.sum(ProtocoloLote.total_sucesso).label("total_sucesso"),
        func.count(ProtocoloLote.id).label("total_lotes")
    ).filter(
        ProtocoloLote.user_id == current_user.id,
        ProtocoloLote.created_at >= month_start
    ).first()

    total_sucesso = stats.total_sucesso or 0
    
    # Pricing
    cost_per_file = 0.02
    monthly_cost = total_sucesso * cost_per_file

    return {
        "monthly_sucesso": total_sucesso,
        "monthly_cost": round(monthly_cost, 2),
        "total_lotes": stats.total_lotes or 0
    }



# ---------------------------------------------------------------------------
# GET /config — API Status
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config(
    current_user=Depends(get_protocolo_user),
):
    """Get current Gemini API configuration status (no secrets exposed)."""
    try:
        from services.gemini_client import GeminiClient, MODELS_PRIORITY
        client = GeminiClient.from_env()
        return {
            "total_keys": client.total_keys,
            "models": MODELS_PRIORITY,
            "status": "ok",
        }
    except Exception as e:
        return {
            "total_keys": 0,
            "models": [],
            "status": f"error: {str(e)}",
        }


# ---------------------------------------------------------------------------
# POST /arquivos/{id}/gravar — Gravar itens do arquivo
# ---------------------------------------------------------------------------

@router.post("/arquivos/{arquivo_id}/gravar")
def gravar_arquivo_route(
    arquivo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Saves extracted atendimentos from a file to the protocolo_itens table."""
    from models import ProtocoloArquivo, ProtocoloLote
    from services.protocolo_service import gravar_arquivo_itens

    arq = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arq:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arq.lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote associado não encontrado")
        
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    try:
        res = gravar_arquivo_itens(db, arquivo_id)
        return res
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# POST /lotes/{id}/gravar-todos — Gravar todos do lote
# ---------------------------------------------------------------------------

@router.post("/lotes/{lote_id}/gravar-todos")
def gravar_lote_route(
    lote_id: int,
    ignore_unsigned: bool = Query(False),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Saves extracted atendimentos from all successful files in a lote."""
    from models import ProtocoloLote
    from services.protocolo_service import gravar_lote_itens

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    try:
        res = gravar_lote_itens(db, lote_id, ignore_unsigned=ignore_unsigned)
        return res
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# GET /itens/exportar — Get all matching items for export
# ---------------------------------------------------------------------------

@router.get("/itens/exportar")
def export_protocolo_itens(
    id_convenio: Optional[int] = Query(None),
    status_conciliacao: Optional[str] = Query(None),
    nome: Optional[str] = Query(None),
    guia: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    assinatura: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Lists and filters all matching saved protocolo items (no limit) for exporting."""
    from models import ProtocoloItem, FaturamentoLote, Agendamento, BaseGuia
    from datetime import datetime

    query = db.query(ProtocoloItem).filter(ProtocoloItem.user_id == current_user.id)

    if id_convenio is not None:
        query = query.filter(ProtocoloItem.id_convenio == id_convenio)
    
    if status_conciliacao:
        query = query.filter(ProtocoloItem.status_conciliacao == status_conciliacao)

    if assinatura:
        query = query.filter(ProtocoloItem.assinatura == assinatura)

    if nome:
        query = query.filter(ProtocoloItem.nome.ilike(f"%{nome}%"))

    if guia:
        query = query.filter(
            (ProtocoloItem.guia.ilike(f"%{guia}%")) | 
            (ProtocoloItem.senha.ilike(f"%{guia}%")) | 
            (ProtocoloItem.guia_prestador.ilike(f"%{guia}%"))
        )

    if data_inicio:
        try:
            dt_ini = datetime.strptime(data_inicio, "%Y-%m-%d").date()
            query = query.filter(ProtocoloItem.data >= dt_ini)
        except Exception:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d").date()
            query = query.filter(ProtocoloItem.data <= dt_fim)
        except Exception:
            pass

    items = query.order_by(ProtocoloItem.data.desc(), ProtocoloItem.id.desc()).all()

    data = []
    for item in items:
        fat_detail = None
        if item.faturamento_rel:
            fat_detail = {
                "id": item.faturamento_rel.id,
                "detalheId": item.faturamento_rel.detalheId,
                "ValorProcedimento": item.faturamento_rel.ValorProcedimento,
                "Guia": item.faturamento_rel.Guia,
                "dataRealizacao": item.faturamento_rel.dataRealizacao.isoformat() if item.faturamento_rel.dataRealizacao else None
            }

        ag_detail = None
        if item.agendamento_rel:
            ag_detail = {
                "id_agendamento": item.agendamento_rel.id_agendamento,
                "Nome_Paciente": item.agendamento_rel.Nome_Paciente,
                "numero_guia": item.agendamento_rel.numero_guia,
                "data": item.agendamento_rel.data.isoformat() if item.agendamento_rel.data else None,
                "nome_procedimento": item.agendamento_rel.nome_procedimento
            }

        bg_detail = None
        if item.base_guia_rel:
            bg_detail = {
                "id": item.base_guia_rel.id,
                "guia": item.base_guia_rel.guia,
                "senha": item.base_guia_rel.senha
            }

        data.append({
            "id": item.id,
            "id_convenio": item.id_convenio,
            "cod_prestador": item.cod_prestador,
            "guia": item.guia,
            "nome": item.nome,
            "carteira": item.carteira,
            "senha": item.senha,
            "data": item.data.isoformat() if item.data else None,
            "assinatura": item.assinatura,
            "guia_prestador": item.guia_prestador,
            "lote_id": item.lote_id,
            "arquivo_id": item.arquivo_id,
            "status_conciliacao": item.status_conciliacao,
            "faturamento": fat_detail,
            "agendamento": ag_detail,
            "base_guia": bg_detail,
            "caminho_arquivo": item.caminho_arquivo,
            "created_at": item.created_at.isoformat() if item.created_at else None
        })

    return {
        "data": data
    }


# ---------------------------------------------------------------------------
# GET /itens — List and filter protocolo_itens
# ---------------------------------------------------------------------------

@router.get("/itens")
def list_protocolo_itens(
    id_convenio: Optional[int] = Query(None),
    status_conciliacao: Optional[str] = Query(None),
    nome: Optional[str] = Query(None),
    guia: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    assinatura: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=250),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Lists, filters, and paginates saved protocolo items."""
    from models import ProtocoloItem, FaturamentoLote, Agendamento, BaseGuia
    from datetime import datetime

    query = db.query(ProtocoloItem).filter(ProtocoloItem.user_id == current_user.id)

    if id_convenio is not None:
        query = query.filter(ProtocoloItem.id_convenio == id_convenio)
    
    if status_conciliacao:
        query = query.filter(ProtocoloItem.status_conciliacao == status_conciliacao)

    if assinatura:
        query = query.filter(ProtocoloItem.assinatura == assinatura)

    if nome:
        query = query.filter(ProtocoloItem.nome.ilike(f"%{nome}%"))

    if guia:
        query = query.filter(
            (ProtocoloItem.guia.ilike(f"%{guia}%")) | 
            (ProtocoloItem.senha.ilike(f"%{guia}%")) | 
            (ProtocoloItem.guia_prestador.ilike(f"%{guia}%"))
        )

    if data_inicio:
        try:
            dt_ini = datetime.strptime(data_inicio, "%Y-%m-%d").date()
            query = query.filter(ProtocoloItem.data >= dt_ini)
        except Exception:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d").date()
            query = query.filter(ProtocoloItem.data <= dt_fim)
        except Exception:
            pass

    total = query.count()

    items = query.order_by(ProtocoloItem.data.desc(), ProtocoloItem.id.desc()).offset(skip).limit(limit).all()

    data = []
    for item in items:
        fat_detail = None
        if item.faturamento_rel:
            fat_detail = {
                "id": item.faturamento_rel.id,
                "detalheId": item.faturamento_rel.detalheId,
                "ValorProcedimento": item.faturamento_rel.ValorProcedimento,
                "Guia": item.faturamento_rel.Guia,
                "dataRealizacao": item.faturamento_rel.dataRealizacao.isoformat() if item.faturamento_rel.dataRealizacao else None
            }

        ag_detail = None
        if item.agendamento_rel:
            ag_detail = {
                "id_agendamento": item.agendamento_rel.id_agendamento,
                "Nome_Paciente": item.agendamento_rel.Nome_Paciente,
                "numero_guia": item.agendamento_rel.numero_guia,
                "data": item.agendamento_rel.data.isoformat() if item.agendamento_rel.data else None,
                "nome_procedimento": item.agendamento_rel.nome_procedimento
            }

        bg_detail = None
        if item.base_guia_rel:
            bg_detail = {
                "id": item.base_guia_rel.id,
                "guia": item.base_guia_rel.guia,
                "senha": item.base_guia_rel.senha
            }

        data.append({
            "id": item.id,
            "id_convenio": item.id_convenio,
            "cod_prestador": item.cod_prestador,
            "guia": item.guia,
            "nome": item.nome,
            "carteira": item.carteira,
            "senha": item.senha,
            "data": item.data.isoformat() if item.data else None,
            "assinatura": item.assinatura,
            "guia_prestador": item.guia_prestador,
            "lote_id": item.lote_id,
            "arquivo_id": item.arquivo_id,
            "status_conciliacao": item.status_conciliacao,
            "faturamento": fat_detail,
            "agendamento": ag_detail,
            "base_guia": bg_detail,
            "caminho_arquivo": item.caminho_arquivo,
            "created_at": item.created_at.isoformat() if item.created_at else None
        })

    return {
        "total": total,
        "data": data
    }


# ---------------------------------------------------------------------------
# POST /itens/conciliar — Conciliar itens de protocolo
# ---------------------------------------------------------------------------

@router.post("/itens/conciliar")
def conciliar_itens_route(
    id_convenio: int = Query(..., description="ID do Convenio (3 ou 6)"),
    faturamento_lote_id: Optional[int] = Query(None, description="Lote de Faturamento ID opcional"),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Triggers the auto-conciliation process for saved items."""
    from services.protocolo_service import conciliar_itens_protocolo

    res = conciliar_itens_protocolo(
        db, 
        user_id=current_user.id, 
        id_convenio=id_convenio, 
        faturamento_lote_id=faturamento_lote_id
    )
    return res

