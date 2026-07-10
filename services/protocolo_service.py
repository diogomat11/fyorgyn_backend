"""
Protocolo Service — Async batch orchestration for PDF extraction.

Manages the lifecycle of lotes (batches):
  create → enqueue → process (background) → complete

Processing runs in a background thread so the HTTP request returns immediately.
"""

import os
import io
import zipfile
import logging
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Upload directory (relative to backend root)
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "protocolo")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "protocolo_output")

# Max files per batch
MAX_FILES_PER_LOTE = 100

# Max file size (10MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

# ZIP split size (90MB)
ZIP_SPLIT_SIZE_BYTES = 90 * 1024 * 1024


def _get_db_session():
    """Create a fresh DB session for background threads."""
    from database import SessionLocal
    return SessionLocal()


def _get_gemini_client():
    """Lazy-load the Gemini client singleton."""
    from services.gemini_client import GeminiClient
    return GeminiClient.from_env()


def recalculate_lote_totals(db: Session, lote_id: int):
    """Force recalculate all totals for a lote from its arquivos using optimized SQL queries."""
    from models import ProtocoloLote, ProtocoloArquivo
    from sqlalchemy import func
    
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        return

    # Aggregate counts in a single query if possible, or simple separate count queries
    counts = db.query(
        ProtocoloArquivo.status,
        func.count(ProtocoloArquivo.id)
    ).filter(ProtocoloArquivo.lote_id == lote_id).group_by(ProtocoloArquivo.status).all()
    
    status_map = dict(counts)
    
    lote.total_arquivos = sum(status_map.values())
    lote.total_sucesso = status_map.get("sucesso", 0)
    lote.total_erro = (
        status_map.get("erro", 0) + 
        status_map.get("falha", 0) + 
        status_map.get("revisao", 0)
    )
    lote.total_processado = sum(
        count for status, count in status_map.items() 
        if status not in ["pendente", "processando"]
    )
    
    db.commit()


# ---------------------------------------------------------------------------
# Lote Creation
# ---------------------------------------------------------------------------

def create_lote(db: Session, user_id: int, files: list, convenio: str = "unimed_goiania") -> dict:
    """
    Create a new lote and save uploaded files to disk.

    Args:
        db: Active DB session
        user_id: ID of the authenticated user
        files: List of UploadFile objects from FastAPI
        convenio: Select of Convênio ('unimed_goiania' or 'ipasgo')

    Returns:
        dict with lote_id and file count
    """
    from models import ProtocoloLote, ProtocoloArquivo

    if len(files) > MAX_FILES_PER_LOTE:
        raise ValueError(f"Máximo de {MAX_FILES_PER_LOTE} arquivos por lote")

    # Create lote record
    lote = ProtocoloLote(
        user_id=user_id,
        status="pending",
        total_arquivos=len(files),
        convenio=convenio,
    )
    db.add(lote)
    db.flush()  # Get lote.id

    # Create upload directory for this lote
    lote_dir = os.path.join(UPLOAD_DIR, str(lote.id))
    os.makedirs(lote_dir, exist_ok=True)

    output_dir = os.path.join(OUTPUT_DIR, str(lote.id))
    os.makedirs(output_dir, exist_ok=True)

    # Save each file and create arquivo records
    for upload_file in files:
        content = upload_file.file.read()
        file_size = len(content)

        if file_size > MAX_FILE_SIZE_BYTES:
            # Create record with error status
            arquivo = ProtocoloArquivo(
                lote_id=lote.id,
                nome_original=upload_file.filename,
                status="erro",
                tamanho_bytes=file_size,
                erro_mensagem=f"Arquivo excede limite de {MAX_FILE_SIZE_BYTES // (1024*1024)}MB",
                caminho_original="",
            )
            db.add(arquivo)
            continue

        # Save to disk
        safe_name = f"{lote.id}_{upload_file.filename}"
        file_path = os.path.join(lote_dir, safe_name)
        with open(file_path, "wb") as f:
            f.write(content)

        arquivo = ProtocoloArquivo(
            lote_id=lote.id,
            nome_original=upload_file.filename,
            status="pendente",
            tamanho_bytes=file_size,
            caminho_original=file_path,
        )
        db.add(arquivo)

    db.commit()
    db.refresh(lote)

    # Start background processing
    thread = threading.Thread(
        target=_process_lote_background,
        args=(lote.id,),
        daemon=True,
    )
    thread.start()

    logger.info(f"Lote {lote.id} created with {len(files)} files. Background processing started.")

    return {
        "lote_id": lote.id,
        "total_arquivos": len(files),
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# Background Processing
# ---------------------------------------------------------------------------

def _process_lote_background(lote_id: int):
    """
    Process all pending files in a lote. Runs in a background thread.
    
    This function creates its own DB session and Gemini client,
    ensuring complete independence from the HTTP request lifecycle.
    """
    from models import ProtocoloLote, ProtocoloArquivo
    from services.extraction_pipeline import process_single_extraction, rename_file_safe

    db = _get_db_session()

    try:
        gemini = _get_gemini_client()
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client for lote {lote_id}: {e}")
        lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
        if lote:
            lote.status = "error"
            lote.updated_at = datetime.utcnow()
            db.commit()
        db.close()
        return

    try:
        # Mark lote as processing
        lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
        if not lote:
            logger.error(f"Lote {lote_id} not found")
            return

        lote.status = "processing"
        lote.updated_at = datetime.utcnow()
        db.commit()

        # Get all pending files
        arquivos = db.query(ProtocoloArquivo).filter(
            ProtocoloArquivo.lote_id == lote_id,
            ProtocoloArquivo.status == "pendente",
        ).all()

        output_dir = os.path.join(OUTPUT_DIR, str(lote_id))
        os.makedirs(output_dir, exist_ok=True)

        total_sucesso = 0
        total_erro = 0

        for arquivo in arquivos:
            try:
                # Check for cancellation before processing each file
                db.refresh(lote)
                if lote.status == "cancelled":
                    logger.info(f"Lote {lote_id} foi cancelado pelo usuário.")
                    break

                # Mark file as processing
                arquivo.status = "processando"
                arquivo.updated_at = datetime.utcnow()
                db.commit()

                # Read PDF bytes
                if not arquivo.caminho_original or not os.path.exists(arquivo.caminho_original):
                     raise FileNotFoundError(f"Arquivo não encontrado: {arquivo.caminho_original}")

                # If IPASGO, perform guide rotation check and fix orientation if needed
                if lote.convenio == "ipasgo":
                    try:
                        from services.pdf_rotator import check_and_fix_rotation
                        check_and_fix_rotation(arquivo.caminho_original, arquivo.caminho_original, logger.info)
                    except Exception as rot_err:
                        logger.error(f"Erro ao rotacionar PDF {arquivo.id}: {rot_err}")

                with open(arquivo.caminho_original, "rb") as f:
                    pdf_bytes = f.read()

                # Call Gemini
                if lote.convenio == "ipasgo":
                    prompt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "services", "prompt_Sadt_IPASGO.yaml")
                    if not os.path.exists(prompt_path):
                        # Fallback path if directory structure differs slightly
                        prompt_path = os.path.join(os.path.dirname(__file__), "prompt_Sadt_IPASGO.yaml")
                    
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        ipasgo_prompt = f.read()
                    
                    from services.gemini_client import IPASGO_RESPONSE_SCHEMA
                    gemini_result = gemini.extract_from_pdf(
                        pdf_bytes,
                        prompt=ipasgo_prompt,
                        response_schema=IPASGO_RESPONSE_SCHEMA
                    )
                else:
                    gemini_result = gemini.extract_from_pdf(pdf_bytes)

                # Store meta about which model/key was used
                meta = gemini_result.pop("_meta", {})
                arquivo.gemini_model_used = meta.get("model", "unknown")
                arquivo.gemini_api_key_index = meta.get("key_index", -1)

                # Map IPASGO fields to standard names expected by the validation and extraction pipeline
                if lote.convenio == "ipasgo":
                    atends_raw = gemini_result.get("ATENDIMENTOS") or []
                    if not atends_raw and gemini_result.get("DATA_AUTORIZACAO"):
                        atends_raw = [{
                            "data": gemini_result.get("DATA_AUTORIZACAO") or "",
                            "assinatura": "Sim"
                        }]
                    
                    normalized_result = {
                        "numeroGuiaPrestador": gemini_result.get("NUMERO_GUIA") or "",
                        "nomeBeneficiario": gemini_result.get("NOME_BENEFICIARIO") or "",
                        "numeroGuiaPrincipal": gemini_result.get("NUMERO_SENHA") or "VAZIO",
                        "carteira": gemini_result.get("CARTEIRA") or "VAZIO",
                        "atendimentos": atends_raw
                    }
                    gemini_result = normalized_result

                # Run extraction pipeline
                pipeline_result = process_single_extraction(gemini_result, convenio=lote.convenio)

                # Store extracted data
                arquivo.numero_guia_prestador = gemini_result.get("numeroGuiaPrestador", "")
                arquivo.nome_beneficiario = pipeline_result.get("nome_beneficiario", "")
                arquivo.numero_guia_principal = gemini_result.get("numeroGuiaPrincipal", "")
                arquivo.carteira = gemini_result.get("carteira", "")
                if arquivo.carteira == "VAZIO":
                    arquivo.carteira = None
                arquivo.atendimentos = gemini_result.get("atendimentos", [])
                arquivo.guia_normalizada = pipeline_result.get("guia_normalizada", "")

                if pipeline_result["success"]:
                    # Rename the physical file
                    nome_final = pipeline_result["nome_final"]
                    arquivo.nome_final = nome_final

                    try:
                        final_path = rename_file_safe(
                            arquivo.caminho_original,
                            output_dir,
                            nome_final,
                        )
                        arquivo.caminho_final = final_path
                        arquivo.status = "sucesso"
                        total_sucesso += 1
                    except Exception as rename_err:
                        arquivo.status = "erro"
                        arquivo.erro_mensagem = f"Erro ao renomear: {str(rename_err)}"
                        total_erro += 1
                else:
                    # Validation/blacklist failed → mark for review
                    arquivo.status = "revisao"
                    arquivo.erro_mensagem = "; ".join(pipeline_result["errors"])
                    # Still build a proposed name for manual edit
                    arquivo.nome_final = pipeline_result.get("nome_final", "")
                    total_erro += 1

            except Exception as e:
                logger.error(f"Error processing file {arquivo.id} ({arquivo.nome_original}): {e}")
                arquivo.status = "erro"
                arquivo.erro_mensagem = str(e)[:500]
                total_erro += 1

            arquivo.updated_at = datetime.utcnow()
            db.commit()

        # Update lote totals only if not cancelled
        if lote_id:
            recalculate_lote_totals(db, lote_id)
            
            # Re-fetch lote to update status
            lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
            if lote and lote.status != "cancelled":
                lote.status = "completed"
                lote.updated_at = datetime.utcnow()
                db.commit()

            logger.info(f"Lote {lote_id} completed and totals recalculated.")

    except Exception as e:
        logger.error(f"Fatal error processing lote {lote_id}: {e}")
        try:
            lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
            if lote:
                lote.status = "error"
                lote.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Status Query
# ---------------------------------------------------------------------------

def get_lote_status(db: Session, lote_id: int) -> Optional[dict]:
    """
    Get full status of a lote including all arquivo details.
    """
    from models import ProtocoloLote, ProtocoloArquivo

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        return None

    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id
    ).order_by(ProtocoloArquivo.id.asc()).all()

    return {
        "lote_id": lote.id,
        "status": lote.status,
        "convenio": lote.convenio,
        "total_arquivos": lote.total_arquivos,
        "total_processado": lote.total_processado,
        "total_sucesso": lote.total_sucesso,
        "total_erro": lote.total_erro,
        "created_at": lote.created_at.isoformat() if lote.created_at else None,
        "updated_at": lote.updated_at.isoformat() if lote.updated_at else None,
        "arquivos": [
            {
                "id": a.id,
                "nome_original": a.nome_original,
                "nome_final": a.nome_final,
                "status": a.status,
                "tamanho_bytes": a.tamanho_bytes,
                "numero_guia_prestador": a.numero_guia_prestador,
                "nome_beneficiario": a.nome_beneficiario,
                "numero_guia_principal": a.numero_guia_principal,
                "atendimentos": a.atendimentos,
                "guia_normalizada": a.guia_normalizada,
                "erro_mensagem": a.erro_mensagem,
                "gemini_model_used": a.gemini_model_used,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in arquivos
        ],
    }


def list_lotes(db: Session, user_id: Optional[int] = None, limit: int = 25, skip: int = 0) -> dict:
    """List lotes with pagination, optionally filtered by user."""
    from models import ProtocoloLote

    query = db.query(ProtocoloLote)
    if user_id:
        query = query.filter(ProtocoloLote.user_id == user_id)

    total = query.count()
    lotes = query.order_by(ProtocoloLote.created_at.desc()).limit(limit).offset(skip).all()

    return {
        "data": [
            {
                "id": l.id,
                "status": l.status,
                "convenio": l.convenio,
                "total_arquivos": l.total_arquivos,
                "total_processado": l.total_processado,
                "total_sucesso": l.total_sucesso,
                "total_erro": l.total_erro,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "updated_at": l.updated_at.isoformat() if l.updated_at else None,
            }
            for l in lotes
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# Reprocess Errors
# ---------------------------------------------------------------------------

def reprocess_errors(db: Session, lote_id: int) -> int:
    """
    Reset errored/review files back to 'pendente' and re-trigger processing.
    Returns the number of files queued for reprocessing.
    """
    from models import ProtocoloLote, ProtocoloArquivo

    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id,
        ProtocoloArquivo.status.in_(["erro", "revisao"]),
    ).all()

    if not arquivos:
        return 0

    for a in arquivos:
        a.status = "pendente"
        a.erro_mensagem = None
        a.updated_at = datetime.utcnow()

    # Reset lote status
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if lote:
        lote.status = "processing"
        lote.updated_at = datetime.utcnow()

    db.commit()

    # Restart background processing
    thread = threading.Thread(
        target=_process_lote_background,
        args=(lote_id,),
        daemon=True,
    )
    thread.start()

    return len(arquivos)


# ---------------------------------------------------------------------------
# Cancel Processing
# ---------------------------------------------------------------------------

def cancel_lote(db: Session, lote_id: int) -> bool:
    """
    Cancel an ongoing processing lote.
    """
    from models import ProtocoloLote

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote or lote.status not in ["pending", "processing"]:
        return False

    lote.status = "cancelled"
    lote.updated_at = datetime.utcnow()
    db.commit()

    return True


# ---------------------------------------------------------------------------
# Update Filename (Manual Edit)
# ---------------------------------------------------------------------------

def update_arquivo_nome(db: Session, arquivo_id: int, novo_nome: str) -> Optional[dict]:
    """Update the final filename of an arquivo (manual override) and validate it."""
    from models import ProtocoloArquivo, ProtocoloLote

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return None

    old_status = arquivo.status

    arquivo.nome_final = novo_nome
    # A manual edit implies manual validation
    arquivo.status = "sucesso"
    arquivo.erro_mensagem = None
    arquivo.updated_at = datetime.utcnow()

    arquivo.updated_at = datetime.utcnow()

    db.commit()
    
    # Recalculate lote totals
    recalculate_lote_totals(db, arquivo.lote_id)
    
    db.refresh(arquivo)

    return {
        "id": arquivo.id,
        "nome_final": arquivo.nome_final,
        "status": arquivo.status,
    }


def update_arquivo_atendimentos(db: Session, arquivo_id: int, atendimentos: list[dict]) -> Optional[dict]:
    """Update the atendimentos JSON of an arquivo and rename final file if needed."""
    from models import ProtocoloArquivo, ProtocoloLote
    from services.extraction_pipeline import build_filename, validate_date, rename_file_safe, normalize_guia_prefix

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return None

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arquivo.lote_id).first()
    if not lote:
        return None

    arquivo.atendimentos = atendimentos
    arquivo.status = "sucesso"  # Promoting to success after manual adjustment
    arquivo.erro_mensagem = None
    arquivo.updated_at = datetime.utcnow()

    # Recalculate proposed final filename based on new date of atendimentos
    first_date = ""
    if atendimentos:
        first_date = atendimentos[0].get("data", "")
        # Normalize date format
        _, normalized = validate_date(first_date)
        if normalized:
            first_date = normalized

    guia_raw = arquivo.numero_guia_prestador or ""
    if lote.convenio != "ipasgo":
        guia_normalizada = normalize_guia_prefix(guia_raw)
    else:
        guia_normalizada = guia_raw

    guia_principal = arquivo.numero_guia_principal or "VAZIO"
    if guia_principal and guia_principal.upper() != "VAZIO":
        if lote.convenio != "ipasgo":
            guia_principal = normalize_guia_prefix(guia_principal)

    novo_nome = build_filename(
        guia_principal,
        guia_normalizada,
        first_date,
        arquivo.nome_beneficiario,
        convenio=lote.convenio,
    )

    if novo_nome != arquivo.nome_final:
        try:
            source_path = arquivo.caminho_final or arquivo.caminho_original
            if source_path and os.path.exists(source_path):
                final_path = rename_file_safe(
                    source_path,
                    OUTPUT_DIR,
                    novo_nome,
                )
                arquivo.caminho_final = final_path
            arquivo.nome_final = novo_nome
        except Exception as rename_err:
            logger.error(f"Erro ao renomear arquivo apos alteracao de data: {str(rename_err)}")
            arquivo.nome_final = novo_nome

    db.commit()
    
    # Recalculate totals
    recalculate_lote_totals(db, arquivo.lote_id)
    
    db.refresh(arquivo)

    return {
        "id": arquivo.id,
        "atendimentos": arquivo.atendimentos,
        "status": arquivo.status,
        "nome_final": arquivo.nome_final
    }


# ---------------------------------------------------------------------------
# Delete Arquivo
# ---------------------------------------------------------------------------

def delete_arquivo(db: Session, arquivo_id: int) -> bool:
    """Delete a single file and update lote totals."""
    from models import ProtocoloArquivo, ProtocoloLote

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return False

    lote_id = arquivo.lote_id
    status = arquivo.status

    db.delete(arquivo)
    db.commit()
    
    # Update lote totals
    recalculate_lote_totals(db, lote_id)

    return True


# ---------------------------------------------------------------------------
# ZIP Download Generation
# ---------------------------------------------------------------------------

def generate_download_zip(db: Session, lote_id: int) -> list[io.BytesIO]:
    """
    Generate ZIP file(s) for all successfully processed files.
    Splits into chunks of ZIP_SPLIT_SIZE_BYTES.

    Returns a list of BytesIO objects (one per ZIP chunk).
    """
    from models import ProtocoloArquivo

    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id,
        ProtocoloArquivo.status == "sucesso",
    ).all()

    if not arquivos:
        return []

    # Collect valid files
    valid_files = []
    for a in arquivos:
        filepath = None
        if a.caminho_final and os.path.exists(a.caminho_final):
            filepath = a.caminho_final
        elif a.caminho_original and os.path.exists(a.caminho_original):
            filepath = a.caminho_original

        if filepath:
            valid_files.append((a.nome_final or a.nome_original, filepath))

    if not valid_files:
        return []

    # Build ZIP(s) with size splitting
    zip_buffers = []
    current_buffer = io.BytesIO()
    current_zip = zipfile.ZipFile(current_buffer, "w", zipfile.ZIP_DEFLATED)
    current_size = 0
    used_names = set()

    for filename, filepath in valid_files:
        file_size = os.path.getsize(filepath)

        # Check if adding this file would exceed the split size
        if current_size + file_size > ZIP_SPLIT_SIZE_BYTES and current_size > 0:
            # Close current ZIP and start a new one
            current_zip.close()
            current_buffer.seek(0)
            zip_buffers.append(current_buffer)

            current_buffer = io.BytesIO()
            current_zip = zipfile.ZipFile(current_buffer, "w", zipfile.ZIP_DEFLATED)
            current_size = 0
            used_names = set()

        # Deduplicate filename to prevent warnings/skips on extraction
        base, ext = os.path.splitext(filename)
        counter = 1
        unique_filename = filename
        while unique_filename.lower() in used_names:
            unique_filename = f"{base}_{counter}{ext}"
            counter += 1
        used_names.add(unique_filename.lower())

        current_zip.write(filepath, arcname=unique_filename)
        current_size += file_size

    # Close the last ZIP
    current_zip.close()
    current_buffer.seek(0)
    zip_buffers.append(current_buffer)

    return zip_buffers


def get_arquivo_file_path(db: Session, arquivo_id: int) -> Optional[tuple[str, str]]:
    """
    Get the file path for individual download.
    Returns (filepath, filename) or None.
    """
    from models import ProtocoloArquivo

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return None

    # Prefer final (renamed) file, fallback to original
    if arquivo.caminho_final and os.path.exists(arquivo.caminho_final):
        return arquivo.caminho_final, arquivo.nome_final or arquivo.nome_original
    elif arquivo.caminho_original and os.path.exists(arquivo.caminho_original):
        return arquivo.caminho_original, arquivo.nome_original

    return None


def gravar_arquivo_itens(db: Session, arquivo_id: int, ignore_unsigned: bool = False) -> dict:
    """
    Saves all atendimentos (sessions) extracted from a ProtocoloArquivo as ProtocoloItem rows.
    """
    from models import ProtocoloArquivo, ProtocoloLote, ProtocoloItem, UserConvenio, BaseGuia
    from datetime import datetime

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        raise ValueError("Arquivo não encontrado")

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == arquivo.lote_id).first()
    if not lote:
        raise ValueError("Lote associado não encontrado")

    # Mapear convenio string para id_convenio
    id_convenio = 3
    if lote.convenio == "ipasgo":
        id_convenio = 6
    elif lote.convenio == "unimed_goiania":
        id_convenio = 3

    # Resolve cod_prestador
    cod_prestador = None
    if lote.user_id:
        user_conv = db.query(UserConvenio).filter(
            UserConvenio.user_id == lote.user_id,
            UserConvenio.id_convenio == id_convenio
        ).first()
        if user_conv:
            cod_prestador = user_conv.cod_prestador

    # Determinar a guia principal e guia prestador
    guia_val = arquivo.guia_normalizada or arquivo.numero_guia_prestador or ""
    senha_val = arquivo.numero_guia_principal
    if senha_val == "VAZIO":
        senha_val = None

    # Tenta achar a relação com base_guias
    base_guia_id = None
    if guia_val:
        bg = db.query(BaseGuia).filter(
            BaseGuia.id_convenio == id_convenio,
            (BaseGuia.guia == guia_val) | (BaseGuia.senha == guia_val)
        ).first()
        if bg:
            base_guia_id = bg.id
    if not base_guia_id and senha_val:
        bg = db.query(BaseGuia).filter(
            BaseGuia.id_convenio == id_convenio,
            (BaseGuia.guia == senha_val) | (BaseGuia.senha == senha_val)
        ).first()
        if bg:
            base_guia_id = bg.id

    # Deletar itens antigos deste arquivo para garantir idempotência
    db.query(ProtocoloItem).filter(ProtocoloItem.arquivo_id == arquivo_id).delete()

    # Criar um ProtocoloItem para cada atendimento
    atendimentos_list = arquivo.atendimentos or []
    items_created = 0

    for atend in atendimentos_list:
        # Se ignore_unsigned for True, ignorar registros que não estejam assinados
        if ignore_unsigned and atend.get("assinatura", "Não") != "Sim":
            continue

        data_str = atend.get("data")
        if not data_str:
            continue
        
        try:
            data_clean = data_str.replace("-", "/").strip()
            dt_obj = datetime.strptime(data_clean, "%d/%m/%Y").date()
        except Exception:
            try:
                dt_obj = datetime.strptime(data_clean, "%Y/%m/%d").date()
            except Exception:
                dt_obj = None

        item = ProtocoloItem(
            user_id=lote.user_id,
            id_convenio=id_convenio,
            cod_prestador=cod_prestador,
            guia=guia_val,
            nome=arquivo.nome_beneficiario,
            carteira=arquivo.carteira,
            senha=senha_val,
            data=dt_obj,
            assinatura=atend.get("assinatura", "Não"),
            guia_prestador=arquivo.numero_guia_prestador,
            lote_id=lote.id,
            arquivo_id=arquivo.id,
            base_guia_id=base_guia_id,
            caminho_arquivo=arquivo.caminho_final or arquivo.caminho_original,
            status_conciliacao="Não Conciliado"
        )
        db.add(item)
        items_created += 1

    arquivo.gravado = True
    db.commit()

    return {
        "arquivo_id": arquivo_id,
        "items_created": items_created,
        "gravado": True
    }


def gravar_lote_itens(db: Session, lote_id: int, ignore_unsigned: bool = False) -> dict:
    """
    Grava os atendimentos de TODOS os arquivos com sucesso de um lote.
    """
    from models import ProtocoloArquivo
    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id,
        ProtocoloArquivo.status == "sucesso"
    ).all()

    total_gravados = 0
    total_itens = 0
    for arq in arquivos:
        res = gravar_arquivo_itens(db, arq.id, ignore_unsigned=ignore_unsigned)
        total_gravados += 1
        total_itens += res["items_created"]

    return {
        "lote_id": lote_id,
        "arquivos_gravados": total_gravados,
        "total_itens_criados": total_itens
    }


def conciliar_itens_protocolo(db: Session, user_id: int, id_convenio: int, faturamento_lote_id: Optional[int] = None) -> dict:
    """
    Algoritmo de auto-conciliação para ProtocoloItem.
    Realiza o match baseado na guia e no match exato da data do atendimento/realização.
    """
    from models import ProtocoloItem, FaturamentoLote, Agendamento

    query_itens = db.query(ProtocoloItem).filter(
        ProtocoloItem.user_id == user_id,
        ProtocoloItem.id_convenio == id_convenio,
        ProtocoloItem.status_conciliacao == "Não Conciliado"
    )
    itens = query_itens.all()

    conciliados_count = 0

    for item in itens:
        if not item.data:
            continue

        # 1. Tentar conciliar com FaturamentoLote
        query_fat = db.query(FaturamentoLote).filter(
            FaturamentoLote.user_id == user_id,
            FaturamentoLote.dataRealizacao == item.data,
            FaturamentoLote.StatusConciliacao != "Conciliado"
        )
        if faturamento_lote_id:
            query_fat = query_fat.filter(FaturamentoLote.id_lote == faturamento_lote_id)

        fats = query_fat.all()
        fat_match = None
        for f in fats:
            if f.Guia and f.Guia.strip():
                f_guia = f.Guia.strip()
                if (item.guia and f_guia in item.guia) or \
                   (item.senha and f_guia in item.senha) or \
                   (item.guia_prestador and f_guia in item.guia_prestador) or \
                   (item.guia and item.guia in f_guia) or \
                   (item.senha and item.senha in f_guia) or \
                   (item.guia_prestador and item.guia_prestador in f_guia):
                    fat_match = f
                    break

        # 2. Tentar conciliar com Agendamento
        ag_match = None
        if fat_match and fat_match.agendamento_id:
            ag_match = db.query(Agendamento).filter(Agendamento.id_agendamento == fat_match.agendamento_id).first()
        else:
            query_ag = db.query(Agendamento).filter(
                Agendamento.user_id == user_id,
                Agendamento.id_convenio == id_convenio,
                Agendamento.data == item.data,
                Agendamento.Status != "Cancelado"
            )
            ags = query_ag.all()
            for ag in ags:
                if ag.numero_guia and ag.numero_guia.strip():
                    ag_guia = ag.numero_guia.strip()
                    if (item.guia and ag_guia in item.guia) or \
                       (item.senha and ag_guia in item.senha) or \
                       (item.guia_prestador and ag_guia in item.guia_prestador) or \
                       (item.guia and item.guia in ag_guia) or \
                       (item.senha and item.senha in ag_guia) or \
                       (item.guia_prestador and item.guia_prestador in ag_guia):
                        ag_match = ag
                        break

        if fat_match or ag_match:
            if fat_match:
                item.faturamento_lote_id = fat_match.id
                fat_match.StatusConciliacao = "Conciliado"
                fat_match.StatusConferencia = 67 # Conferido
                
                if fat_match.agendamento_id:
                    item.agendamento_id = fat_match.agendamento_id

            if ag_match:
                item.agendamento_id = ag_match.id_agendamento
                if fat_match and not fat_match.agendamento_id:
                    fat_match.agendamento_id = ag_match.id_agendamento

            item.status_conciliacao = "Conciliado"
            conciliados_count += 1

    db.commit()
    return {
        "status": "success",
        "processed": len(itens),
        "conciliated": conciliados_count
    }
