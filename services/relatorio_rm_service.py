"""
Service for Relatório Médico Extraction (Gestão Terapias).

Handles:
- Downloading medical report files from URL
- Sending files + prompt to Gemini AI for therapy extraction
- Parsing Gemini responses into structured data
- CRUD operations on relatorios_medicos_extracao table
- Status calculation (TOTAL / PARCIAL / NAO_EXTRAIDO)
"""

import os
import json
import logging
import tempfile
import threading
from typing import Optional

import httpx
import yaml
from sqlalchemy.orm import Session
from sqlalchemy import desc

from models import RelatorioMedicoExtracao
from database import SessionLocal

logger = logging.getLogger(__name__)

# Global semaphore to limit concurrent Gemini extractions to 2
_gemini_semaphore = threading.Semaphore(2)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Standard therapy areas (keys match DB column suffixes)
AREAS_PADRAO = {
    "PSICOLOGIA": "carga_psicologia",
    "FISIOTERAPIA": "carga_fisioterapia",
    "TERAPIA OCUPACIONAL": "carga_terapia_ocupacional",
    "PSICOPEDAGOGIA": "carga_psicopedagogia",
    "FONOAUDIOLOGIA": "carga_fonoaudiologia",
    "PSICOMOTRICIDADE": "carga_psicomotricidade",
    "MUSICOTERAPIA": "carga_musicoterapia",
    "AVALIACAO NEUROPSICOLOGICA": "carga_avaliacao_neuropsicologica",
    "NUTRICAO": "carga_nutricao",
}

# MIME types by extension
MIME_MAP = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# Response schema for structured JSON output from Gemini
RM_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "id_paciente": {"type": "STRING", "description": "ID do paciente"},
        "areas_extraidas": {
            "type": "ARRAY",
            "description": "Lista de áreas terapêuticas extraídas",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "Area": {"type": "STRING", "description": "Nome da área terapêutica"},
                    "area_carga_horaria": {"type": "INTEGER", "description": "Carga horária"},
                    "tipo_carga_horaria": {"type": "STRING", "description": "semanal, mensal ou null"},
                },
            },
        },
        "itens_ignorados": {
            "type": "ARRAY",
            "description": "Áreas não padrão encontradas",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "Area": {"type": "STRING", "description": "Nome da área"},
                    "area_carga_horaria": {"type": "INTEGER", "description": "Carga horária"},
                    "tipo_carga_horaria": {"type": "STRING", "description": "semanal, mensal ou null"},
                },
            },
        },
    },
    "required": ["id_paciente", "areas_extraidas"],
}


# ---------------------------------------------------------------------------
# Prompt Loading
# ---------------------------------------------------------------------------

def _load_prompt_yaml() -> str:
    """Load the Gemini prompt YAML from the services directory."""
    prompt_path = os.path.join(os.path.dirname(__file__), "gemini_rm_prompt.yaml")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# File Download
# ---------------------------------------------------------------------------

def download_file_from_url(url: str) -> tuple[bytes, str]:
    """
    Download a file from a URL into memory.
    
    Returns:
        (file_bytes, mime_type)
    
    Raises:
        ValueError on download failure or unsupported file type.
    """
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise ValueError(f"Falha ao baixar arquivo da URL: {e}")

    # Determine MIME type from URL extension
    url_lower = url.lower().split("?")[0]  # strip query params
    ext = os.path.splitext(url_lower)[1]
    mime_type = MIME_MAP.get(ext, "application/octet-stream")

    if mime_type == "application/octet-stream":
        # Try content-type header
        ct = response.headers.get("content-type", "").lower()
        if "pdf" in ct:
            mime_type = "application/pdf"
        elif "jpeg" in ct or "jpg" in ct:
            mime_type = "image/jpeg"
        elif "png" in ct:
            mime_type = "image/png"

    logger.info(f"Downloaded file from URL: {len(response.content)} bytes, mime={mime_type}")
    return response.content, mime_type


# ---------------------------------------------------------------------------
# Gemini Extraction
# ---------------------------------------------------------------------------

def extract_therapies_from_file(file_bytes: bytes, mime_type: str, id_paciente: str) -> dict:
    """
    Send a medical report file + prompt to Gemini and return structured extraction.
    
    Returns:
        Parsed dict with areas_extraidas, itens_ignorados, etc.
    
    Raises:
        ValueError on extraction failure.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ValueError("google-genai package not installed. Run: pip install google-genai")

    from services.gemini_client import GeminiClient, MODELS_PRIORITY, BACKOFF_BASE_SECONDS

    # Load prompt and inject id_paciente
    prompt_text = _load_prompt_yaml()
    prompt_text += f"\n\n--- DADOS DA REQUISIÇÃO ---\nid_paciente: {id_paciente}\n"

    # Get client with key rotation
    client_wrapper = GeminiClient.from_env()
    
    max_key_rotations = client_wrapper.total_keys * 2
    last_error = None

    for rotation in range(max_key_rotations):
        api_key = client_wrapper._next_key()
        key_index = (client_wrapper._current_key_idx - 1) % client_wrapper.total_keys

        # Apenas utilizar gemini-2.5-flash conforme solicitado
        for model_name in ["gemini-2.5-flash"]:
            try:
                logger.info(
                    f"RM Extraction attempt {rotation + 1}/{max_key_rotations}: "
                    f"model={model_name}, key_index={key_index}"
                )

                client = genai.Client(api_key=api_key)

                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Content(
                            parts=[
                                types.Part.from_text(text=prompt_text),
                                types.Part.from_bytes(
                                    data=file_bytes,
                                    mime_type=mime_type,
                                ),
                            ]
                        )
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RM_RESPONSE_SCHEMA,
                        temperature=0.1,
                    ),
                )

                raw_text = response.text
                if not raw_text:
                    raise ValueError(f"Empty response from {model_name}")

                result = json.loads(raw_text)
                logger.info(f"RM Extraction successful: model={model_name}, areas={len(result.get('areas_extraidas', []))}")
                return result

            except Exception as e:
                error_str = str(e).lower()
                last_error = e

                if "429" in str(e) or "resource_exhausted" in error_str:
                    import time
                    wait = BACKOFF_BASE_SECONDS * (rotation + 1)
                    logger.warning(f"Rate limit on key {key_index}, model {model_name}. Backing off {wait:.1f}s...")
                    time.sleep(wait)
                    continue

                if "not found" in error_str or "not available" in error_str:
                    logger.warning(f"Model {model_name} unavailable, trying fallback...")
                    continue

                logger.error(f"Gemini RM error (key={key_index}, model={model_name}): {e}")
                continue

    raise ValueError(f"All Gemini attempts failed. Last error: {last_error}")


# ---------------------------------------------------------------------------
# Status Calculation
# ---------------------------------------------------------------------------

def calculate_status(areas_extraidas: list, itens_ignorados: list) -> str:
    """
    Calculate extraction status based on completeness.
    
    TOTAL: At least one standard area extracted with carga > 0, no ignored items.
    PARCIAL: Some areas extracted but has ignored items or missing cargas.
    NAO_EXTRAIDO: No areas extracted or all cargas are 0/null.
    """
    if not areas_extraidas:
        return "NAO_EXTRAIDO"

    has_valid_area = any(
        a.get("area_carga_horaria", 0) and a.get("area_carga_horaria", 0) > 0
        for a in areas_extraidas
    )

    if not has_valid_area:
        return "NAO_EXTRAIDO"

    if itens_ignorados:
        return "PARCIAL"

    # Check if any area has carga = 0 (found but no hours)
    has_zero_carga = any(
        a.get("area_carga_horaria", 0) == 0
        for a in areas_extraidas
    )

    if has_zero_carga:
        return "PARCIAL"

    return "TOTAL"


# ---------------------------------------------------------------------------
# Parse Gemini Response → DB Fields
# ---------------------------------------------------------------------------

def parse_gemini_to_db_fields(gemini_result: dict) -> dict:
    """
    Convert Gemini structured response to flat DB column values.
    
    Returns dict with keys matching RelatorioMedicoExtracao columns.
    """
    areas = gemini_result.get("areas_extraidas", [])
    ignorados = gemini_result.get("itens_ignorados", [])

    # Initialize all cargas to None
    db_fields = {col: None for col in AREAS_PADRAO.values()}
    db_fields["tipo_carga_horaria"] = None
    db_fields["itens_ignorados"] = ignorados if ignorados else None

    for area in areas:
        area_name = (area.get("Area") or "").upper().strip()
        carga = area.get("area_carga_horaria", 0)
        tipo = area.get("tipo_carga_horaria")

        if area_name in AREAS_PADRAO:
            db_col = AREAS_PADRAO[area_name]
            db_fields[db_col] = carga if carga else 0

            # Use the first non-null tipo_carga_horaria found
            if tipo and not db_fields["tipo_carga_horaria"]:
                db_fields["tipo_carga_horaria"] = tipo.lower() if tipo else None

    db_fields["status_extracao"] = calculate_status(areas, ignorados)
    return db_fields


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------

def queue_extraction(
    db: Session,
    user_id: int,
    id_paciente: str,
    url_arquivo: str,
    background_tasks,
    nome_paciente: Optional[str] = None,
    id_relatorio: Optional[str] = None,
) -> dict:
    """
    Creates an extraction record with status NAO_PROCESSADO and queues a background task.
    """
    record = RelatorioMedicoExtracao(
        user_id=user_id,
        id_paciente=id_paciente,
        nome_paciente=nome_paciente,
        id_relatorio=id_relatorio,
        url_arquivo=url_arquivo,
        status_extracao="NAO_PROCESSADO"
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # Queue the background processing
    background_tasks.add_task(_process_extraction_bg, record.id)

    logger.info(f"RM Extraction queued: id={record.id}, paciente={id_paciente}")
    return _record_to_dict(record)


def _process_extraction_bg(extraction_id: int):
    """
    Background task to process an extraction.
    Limits concurrency using a semaphore to avoid overloading the API.
    """
    db = SessionLocal()
    try:
        record = db.query(RelatorioMedicoExtracao).filter(RelatorioMedicoExtracao.id == extraction_id).first()
        if not record:
            return

        record.status_extracao = "EM_PROCESSAMENTO"
        db.commit()

        # Limit concurrency
        with _gemini_semaphore:
            logger.info(f"Starting background extraction for id={extraction_id}")
            # Step 1: Download file
            file_bytes, mime_type = download_file_from_url(record.url_arquivo)

            # Step 2: Extract via Gemini
            gemini_result = extract_therapies_from_file(file_bytes, mime_type, record.id_paciente)

            # Step 3: Parse to DB fields
            db_fields = parse_gemini_to_db_fields(gemini_result)

            # Step 4: Save to DB
            for k, v in db_fields.items():
                setattr(record, k, v)
                
            db.commit()
            logger.info(f"Completed background extraction for id={extraction_id}, status={record.status_extracao}")
            
    except Exception as e:
        logger.error(f"Error in background extraction id={extraction_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
            
        try:
            # We open a fresh connection just in case the previous one was entirely closed by Supabase (OperationalError)
            db.close()
            db = SessionLocal()
            record = db.query(RelatorioMedicoExtracao).filter(RelatorioMedicoExtracao.id == extraction_id).first()
            if record:
                record.status_extracao = "ERRO"
                db.commit()
        except Exception as e2:
            logger.error(f"Critical error failing to set ERRO status for id={extraction_id}: {e2}")
    finally:
        db.close()


def list_extractions(
    db: Session,
    user_id: int,
    id_paciente: Optional[str] = None,
    area_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List extractions for the current user with optional filters."""
    query = db.query(RelatorioMedicoExtracao).filter(
        RelatorioMedicoExtracao.user_id == user_id
    )

    if id_paciente:
        query = query.filter(RelatorioMedicoExtracao.id_paciente == id_paciente)

    if status_filter:
        query = query.filter(RelatorioMedicoExtracao.status_extracao == status_filter)

    if area_filter and area_filter in AREAS_PADRAO:
        col_name = AREAS_PADRAO[area_filter]
        col = getattr(RelatorioMedicoExtracao, col_name)
        query = query.filter(col.isnot(None), col > 0)

    total = query.count()
    query = query.order_by(desc(RelatorioMedicoExtracao.created_at))
    records = query.offset(skip).limit(limit).all()
    
    return {
        "data": [_record_to_dict(r) for r in records],
        "total": total,
        "skip": skip,
        "limit": limit
    }


def get_extraction(db: Session, extraction_id: int) -> Optional[dict]:
    """Get a single extraction by ID."""
    record = db.query(RelatorioMedicoExtracao).filter(
        RelatorioMedicoExtracao.id == extraction_id
    ).first()
    return _record_to_dict(record) if record else None


def update_extraction(db: Session, extraction_id: int, updates: dict) -> Optional[dict]:
    """Update extraction fields (manual adjustment by user)."""
    record = db.query(RelatorioMedicoExtracao).filter(
        RelatorioMedicoExtracao.id == extraction_id
    ).first()
    if not record:
        return None

    # Allowed update fields
    allowed_fields = set(AREAS_PADRAO.values()) | {"tipo_carga_horaria", "nome_paciente", "id_paciente"}
    for key, value in updates.items():
        if key in allowed_fields:
            setattr(record, key, value)

    # Recalculate status after manual edit
    areas = []
    for area_name, col_name in AREAS_PADRAO.items():
        carga = getattr(record, col_name)
        if carga is not None:
            areas.append({"Area": area_name, "area_carga_horaria": carga})
    
    ignorados = record.itens_ignorados or []
    record.status_extracao = calculate_status(areas, ignorados)

    db.commit()
    db.refresh(record)
    return _record_to_dict(record)


def delete_extraction(db: Session, extraction_id: int) -> bool:
    """Delete an extraction record."""
    record = db.query(RelatorioMedicoExtracao).filter(
        RelatorioMedicoExtracao.id == extraction_id
    ).first()
    if not record:
        return False

    db.delete(record)
    db.commit()
    return True

def resume_stuck_extractions(db: Session):
    """
    On startup, find any extractions that were left in NAO_PROCESSADO or EM_PROCESSAMENTO
    state due to a server crash, and spawn threads to resume them.
    """
    stuck_records = db.query(RelatorioMedicoExtracao).filter(
        RelatorioMedicoExtracao.status_extracao.in_(["NAO_PROCESSADO", "EM_PROCESSAMENTO"])
    ).all()
    
    if stuck_records:
        logger.info(f"Resuming {len(stuck_records)} stuck extractions on startup...")
        for r in stuck_records:
            r.status_extracao = "NAO_PROCESSADO" # Reset to queue state
            db.commit()
            threading.Thread(target=_process_extraction_bg, args=(r.id,), daemon=True).start()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_to_dict(record: RelatorioMedicoExtracao) -> dict:
    """Convert a SQLAlchemy record to a serializable dict."""
    return {
        "id": record.id,
        "user_id": record.user_id,
        "id_paciente": record.id_paciente,
        "nome_paciente": record.nome_paciente,
        "id_relatorio": record.id_relatorio,
        "url_arquivo": record.url_arquivo,
        "carga_psicologia": record.carga_psicologia,
        "carga_fisioterapia": record.carga_fisioterapia,
        "carga_terapia_ocupacional": record.carga_terapia_ocupacional,
        "carga_psicopedagogia": record.carga_psicopedagogia,
        "carga_fonoaudiologia": record.carga_fonoaudiologia,
        "carga_psicomotricidade": record.carga_psicomotricidade,
        "carga_musicoterapia": record.carga_musicoterapia,
        "carga_avaliacao_neuropsicologica": record.carga_avaliacao_neuropsicologica,
        "tipo_carga_horaria": record.tipo_carga_horaria,
        "status_extracao": record.status_extracao,
        "itens_ignorados": record.itens_ignorados,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }
