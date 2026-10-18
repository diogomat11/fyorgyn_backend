"""Utilitários de mídia para servir documentos (PDF/imagens) inline.

O Evoluir armazena relatórios também como imagem (.jpg/.jpeg/.png); servir
essas extensões com Content-Type application/pdf quebra a visualização no
iframe do frontend."""
import os

EXTENSION_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_SERVABLE_CONTENT_TYPES = set(EXTENSION_MEDIA_TYPES.values())


def infer_media_type(url, fallback="application/pdf", upstream_content_type=None) -> str:
    """Media type para servir um documento inline.

    Prioridade: extensão da URL → Content-Type do upstream (se servível) → fallback."""
    ext = os.path.splitext((url or "").split("?")[0].lower())[1]
    by_ext = EXTENSION_MEDIA_TYPES.get(ext)
    if by_ext:
        return by_ext
    if upstream_content_type:
        ct = str(upstream_content_type).split(";")[0].strip().lower()
        if ct in _SERVABLE_CONTENT_TYPES:
            return ct
    return fallback


def inline_filename(prefix: str, doc_id, url: str) -> str:
    """Filename do Content-Disposition inline preservando a extensão original."""
    ext = os.path.splitext((url or "").split("?")[0])[1] or ".pdf"
    return f"{prefix}_{doc_id}{ext}"
