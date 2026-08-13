"""
backend/services/storage_service.py — Abstracao sobre o Supabase Storage.

Substitui o armazenamento em filesystem local (uploads/) por buckets do Supabase.
Buckets (privados, acesso via service role + signed URLs):
  - 'anexos'          -> anexos de jobs (RM/AI/RC), vida curta (~24h)
  - 'protocolo-input' -> PDFs originais do modulo Protocolo
  - 'protocolo-output'-> PDFs renomeados do modulo Protocolo

Usa a REST API do Storage (https://supabase.com/docs/reference/storage) com a
service role key. Dependencias: requests (ja no projeto). Sem SDK extra.

Fallback: se SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY nao estiverem definidos,
as funcoes de anexo degradam para filesystem local (dev / transicao). Assim o
codigo pode ser deployado antes da criacao dos buckets. Ver DEPLOY.md (Fase 2).
"""

import os
import re
import uuid
import logging
from typing import Optional, List, Dict, Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
_STORAGE_BASE = f"{SUPABASE_URL}/storage/v1" if SUPABASE_URL else ""

# Opt-in explicito: so usa o Supabase Storage quando USE_SUPABASE_STORAGE=1 e as
# credenciais existirem. Isso evita que a presenca de SUPABASE_URL/SERVICE_ROLE_KEY
# (que ja estao no .env para outros fins) ative o Storage antes dos buckets serem
# criados. Ligar apos criar os buckets (ver DEPLOY.md secao 3.1).
_USE_STORAGE = os.getenv("USE_SUPABASE_STORAGE", "").strip().lower() in ("1", "true", "yes", "on")

BUCKET_ANEXOS = "anexos"
BUCKET_PROTOCOLO_IN = "protocolo-input"
BUCKET_PROTOCOLO_OUT = "protocolo-output"

# TTL da signed URL de anexos: 7 dias (o objeto e deletado ~24h apos o job
# concluir, entao a URL nao e necessaria alem disso).
ANEXO_SIGNED_TTL = 7 * 24 * 3600

_LOCAL_ANEXOS_DIR = os.path.join("uploads", "anexos")


def is_enabled() -> bool:
    """True quando o Storage esta explicitamente habilitado (USE_SUPABASE_STORAGE=1)
    E configurado (SUPABASE_URL + service role key). Caso contrario, degrada para
    filesystem local (dev/transicao)."""
    return _USE_STORAGE and bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


# ---------------------------------------------------------------------------
# Headers / low-level
# ---------------------------------------------------------------------------
def _headers(content_type: Optional[str] = None, upsert: bool = False) -> Dict[str, str]:
    h = {"Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}
    if content_type:
        h["Content-Type"] = content_type
    if upsert:
        h["x-upsert"] = "true"
    return h


def upload_bytes(bucket: str, key: str, data: bytes,
                 content_type: str = "application/octet-stream", upsert: bool = False) -> str:
    """Upload de bytes. Retorna a object key."""
    url = f"{_STORAGE_BASE}/object/{bucket}/{key}"
    resp = requests.post(url, headers=_headers(content_type, upsert=upsert), data=data, timeout=120)
    if resp.status_code >= 300:
        raise RuntimeError(f"Storage upload falhou [{bucket}/{key}]: {resp.status_code} {resp.text[:200]}")
    logger.info(f"Storage: upload ok -> {bucket}/{key} ({len(data)} bytes)")
    return key


def delete_object(bucket: str, key: str) -> bool:
    """Deleta um objeto. idempotente (404 nao e erro)."""
    url = f"{_STORAGE_BASE}/object/{bucket}/{key}"
    try:
        resp = requests.delete(url, headers=_headers(), timeout=30)
    except Exception as e:
        logger.error(f"Storage: erro de rede ao deletar {bucket}/{key}: {e}")
        return False
    if resp.status_code >= 300 and resp.status_code != 404:
        logger.error(f"Storage: erro ao deletar {bucket}/{key}: {resp.status_code} {resp.text[:200]}")
        return False
    return True


def create_signed_url(bucket: str, key: str, expires_in: int = 3600) -> str:
    """Gera uma signed URL (buckets privados)."""
    url = f"{_STORAGE_BASE}/object/sign/{bucket}/{key}"
    resp = requests.post(url, headers=_headers("application/json"),
                         json={"expiresIn": expires_in}, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Storage signed URL falhou [{bucket}/{key}]: {resp.status_code} {resp.text[:200]}")
    data = resp.json() if resp.content else {}
    signed = data.get("signedURL") or data.get("signedUrl") or ""
    if signed and signed.startswith("/"):
        signed = f"{SUPABASE_URL}{signed}"
    return signed


def get_public_url(bucket: str, key: str) -> str:
    """URL publica (somente buckets publicos)."""
    return f"{_STORAGE_BASE}/object/public/{bucket}/{key}"


def list_objects(bucket: str, prefix: str = "", limit: int = 1000) -> List[Dict[str, Any]]:
    """Lista objetos com metadata (id, name, created_at, metadata.size)."""
    url = f"{_STORAGE_BASE}/object/list/{bucket}"
    try:
        resp = requests.post(url, headers=_headers("application/json"),
                             json={"prefix": prefix, "limit": limit}, timeout=30)
    except Exception as e:
        logger.error(f"Storage: erro de rede ao listar {bucket}/{prefix}: {e}")
        return []
    if resp.status_code >= 300:
        logger.error(f"Storage: erro ao listar {bucket}/{prefix}: {resp.status_code}")
        return []
    return resp.json() if resp.content else []


def get_object_bytes(bucket: str, key: str) -> bytes:
    """Baixa os bytes de um objeto (uso: streaming de download no backend)."""
    url = f"{_STORAGE_BASE}/object/{bucket}/{key}"
    resp = requests.get(url, headers=_headers(), timeout=120)
    if resp.status_code >= 300:
        raise RuntimeError(f"Storage get falhou [{bucket}/{key}]: {resp.status_code}")
    return resp.content


# ---------------------------------------------------------------------------
# Helpers de chave / nome
# ---------------------------------------------------------------------------
def _safe_name(filename: str) -> str:
    name = (filename or "arquivo").replace(" ", "")
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name


def _unique_key(filename: str) -> str:
    return f"{uuid.uuid4().hex}_{_safe_name(filename)}"


def key_from_anexo_url(url: str) -> str:
    """Extrai a object key de uma URL do bucket 'anexos' (signed ou publica).
    Retorna vazio se a URL nao referenciar o bucket 'anexos'."""
    if not isinstance(url, str) or not url:
        return ""
    m = re.search(r"/anexos/([^?]+)", url)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# API de alto nivel — ANEXOS (jobs)
# ---------------------------------------------------------------------------
def _ensure_local_anexos() -> str:
    os.makedirs(_LOCAL_ANEXOS_DIR, exist_ok=True)
    return _LOCAL_ANEXOS_DIR


def save_anexo(filename: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Salva um anexo de job e retorna uma URL utilizavel pelo frontend.
    - Storage habilitado: devolve uma signed URL absoluta do bucket 'anexos'.
    - Storage desabilitado (dev/transicao): salva em uploads/anexos/ e devolve
      uma URL relativa /uploads/anexos/... (mantem o StaticFiles mount antigo).
    """
    if is_enabled():
        key = _unique_key(filename)
        upload_bytes(BUCKET_ANEXOS, key, data, content_type)
        return create_signed_url(BUCKET_ANEXOS, key, expires_in=ANEXO_SIGNED_TTL)

    # Fallback local
    _ensure_local_anexos()
    local_name = _unique_key(filename)
    path = os.path.join(_LOCAL_ANEXOS_DIR, local_name)
    with open(path, "wb") as f:
        f.write(data)
    return f"/uploads/anexos/{local_name}"


def delete_anexo_by_url(url: str) -> bool:
    """Deleta um anexo a partir da URL armazenada (signed URL do Storage ou
    path local legado). Idempotente."""
    if not isinstance(url, str) or not url:
        return False
    # Storage (Supabase): URL http que referencia o bucket 'anexos'.
    # Atencao: signed URLs do Supabase usam /object/sign/anexos/<key>?token=
    # (SEM o segmento /storage/v1 no path de acesso) — por isso detectamos
    # pelo marcador /anexos/, nao por /storage/.
    if url.startswith("http") and "/anexos/" in url:
        if not is_enabled():
            return False
        key = key_from_anexo_url(url)
        return delete_object(BUCKET_ANEXOS, key) if key else False
    # Legacy local
    if url.startswith("/uploads/anexos/"):
        local_path = url.lstrip("/")
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
                return True
            except Exception as e:
                logger.error(f"Storage: erro ao remover local {local_path}: {e}")
                return False
    return False


def cleanup_orphan_anexos(max_age_seconds: int = 48 * 3600) -> int:
    """Remove anexos orfaos (sem job) com mais de max_age_seconds.
    No Storage: varre list_objects pelo created_at. Local: pelo mtime."""
    from datetime import datetime, timezone
    removed = 0
    if is_enabled():
        now = datetime.now(timezone.utc)
        for obj in list_objects(BUCKET_ANEXOS):
            created = obj.get("created_at")
            name = obj.get("name")
            if not name or not created:
                continue
            try:
                # Supabase retorna ISO8601 (ex.: 2025-01-31T12:00:00.000Z)
                ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if (now - ct).total_seconds() > max_age_seconds:
                    if delete_object(BUCKET_ANEXOS, name):
                        removed += 1
            except Exception:
                continue
        return removed

    # Legacy local
    if os.path.exists(_LOCAL_ANEXOS_DIR):
        import time
        cutoff = time.time() - max_age_seconds
        for fn in os.listdir(_LOCAL_ANEXOS_DIR):
            p = os.path.join(_LOCAL_ANEXOS_DIR, fn)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                try:
                    os.remove(p)
                    removed += 1
                except Exception:
                    pass
    return removed
