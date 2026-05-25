"""
Routes for the Gestão Terapias module (Medical Report extraction via Gemini AI).

Endpoints:
    POST   /api/relatorios/extrair     — Extract therapies from a medical report URL
    GET    /api/relatorios             — List extractions for current user
    GET    /api/relatorios/{id}        — Get single extraction detail
    PUT    /api/relatorios/{id}        — Update extraction (manual adjustment)
    DELETE /api/relatorios/{id}        — Delete extraction record
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user

router = APIRouter(
    prefix="/relatorios",
    tags=["Gestão Terapias"],
)


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class ExtrairRequest(BaseModel):
    id_paciente: str
    url_arquivo: str
    nome_paciente: Optional[str] = None
    id_relatorio: Optional[str] = None


class UpdateExtractionRequest(BaseModel):
    carga_psicologia: Optional[int] = None
    carga_fisioterapia: Optional[int] = None
    carga_terapia_ocupacional: Optional[int] = None
    carga_psicopedagogia: Optional[int] = None
    carga_fonoaudiologia: Optional[int] = None
    carga_psicomotricidade: Optional[int] = None
    carga_musicoterapia: Optional[int] = None
    carga_avaliacao_neuropsicologica: Optional[int] = None
    carga_nutricao: Optional[int] = None
    tipo_carga_horaria: Optional[str] = None
    nome_paciente: Optional[str] = None
    id_paciente: Optional[str] = None


# ---------------------------------------------------------------------------
# POST /extrair — Extract therapies from URL
# ---------------------------------------------------------------------------

@router.post("/extrair")
def extrair_relatorio(
    body: ExtrairRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Queue a medical report extraction. The record is created immediately
    and processing continues in the background.
    """
    from services.relatorio_rm_service import queue_extraction

    try:
        result = queue_extraction(
            db=db,
            user_id=current_user.id,
            id_paciente=body.id_paciente,
            url_arquivo=body.url_arquivo,
            background_tasks=background_tasks,
            nome_paciente=body.nome_paciente,
            id_relatorio=body.id_relatorio,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na extração: {str(e)}")


# ---------------------------------------------------------------------------
# GET / — List extractions
# ---------------------------------------------------------------------------

@router.get("/")
def listar_relatorios(
    id_paciente: Optional[str] = Query(None),
    area: Optional[str] = Query(None, description="Filter by standard area (e.g. PSICOLOGIA)"),
    status: Optional[str] = Query(None, description="Filter by status (TOTAL, PARCIAL, NAO_EXTRAIDO)"),
    limit: int = Query(50, ge=1, le=100000),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all therapy extractions for the current user."""
    from services.relatorio_rm_service import list_extractions

    return list_extractions(
        db=db,
        user_id=current_user.id,
        id_paciente=id_paciente,
        area_filter=area,
        status_filter=status,
        limit=limit,
        skip=skip,
    )


# ---------------------------------------------------------------------------
# GET /{id} — Get single extraction
# ---------------------------------------------------------------------------

@router.get("/{extraction_id}")
def get_relatorio(
    extraction_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get details of a single therapy extraction."""
    from models import RelatorioMedicoExtracao
    
    result = db.query(RelatorioMedicoExtracao).filter(RelatorioMedicoExtracao.id == extraction_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Extração não encontrada")
        
    if not current_user.is_admin and result.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para esta extração.")
        
    return result


# ---------------------------------------------------------------------------
# PUT /{id} — Update extraction (manual adjustment)
# ---------------------------------------------------------------------------

@router.put("/{extraction_id}")
def atualizar_relatorio(
    extraction_id: int,
    body: UpdateExtractionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update therapy extraction values (manual user adjustment after AI extraction)."""
    from services.relatorio_rm_service import update_extraction
    from models import RelatorioMedicoExtracao

    result = db.query(RelatorioMedicoExtracao).filter(RelatorioMedicoExtracao.id == extraction_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Extração não encontrada")
        
    if not current_user.is_admin and result.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para esta extração.")

    updates = body.dict(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    result = update_extraction(db, extraction_id, updates)
    return result


# ---------------------------------------------------------------------------
# DELETE /{id} — Delete extraction
# ---------------------------------------------------------------------------

@router.delete("/{extraction_id}")
def deletar_relatorio(
    extraction_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a therapy extraction record."""
    from services.relatorio_rm_service import delete_extraction
    from models import RelatorioMedicoExtracao

    result = db.query(RelatorioMedicoExtracao).filter(RelatorioMedicoExtracao.id == extraction_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Extração não encontrada")
        
    if not current_user.is_admin and result.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para esta extração.")

    success = delete_extraction(db, extraction_id)
    return {"message": "Extração removida com sucesso"}
