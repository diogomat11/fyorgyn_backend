"""
backend/routes/crm.py — Rota orquestradora de importação e upsert de CRM no PostgreSQL por lotes
"""

import os
import logging
from datetime import datetime
from typing import Optional, List

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from dependencies import get_current_user

from cache import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crm", tags=["CRM"])

BACKEND_WORKER_URL = os.getenv("BACKEND_WORKER_URL", "http://localhost:8001")
BPO_API_KEY = os.getenv("BPO_API_KEY", "bpo_secret_api_key_2026")


class ImportarCRMRequest(BaseModel):
    uf: str = Field(..., description="UF de busca (obrigatório, ex: GO)", min_length=2, max_length=2)
    nome: Optional[str] = Field(None, description="Nome do médico (opcional para admin)")
    registro: Optional[str] = Field(None, description="Número do CRM (opcional para admin)")
    pagina_inicial: int = Field(1, description="Página inicial para o lote (padrão 1)", ge=1)
    max_paginas: int = Field(49, description="Quantidade máxima de páginas por lote (máximo 49)", ge=1, le=49)
    auto_loop_lotes: bool = Field(False, description="Se True, executa lotes sucessivos até exaurir o total de páginas")


@router.post("/consulta", status_code=status.HTTP_200_OK)
def importar_medicos_crm(
    req: ImportarCRMRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),

):
    """
    Rota de IMPORTAÇÃO de médicos do portal CFM para a tabela `corpo_clinico`.
    Executa por lotes de no máximo 49 páginas para evitar estouro de timeout/páginas.
    """
    uf_upper = req.uf.upper()

    # 1. Gate de permissão
    if not current_user.is_admin and not req.nome and not req.registro:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Usuários não-administradores devem informar o Nome ou o número do CRM.",
        )

    # Lookup do conselho "CRM"
    res_conselho = db.execute(
        text("SELECT nome_conselho FROM public.conselhos WHERE nome_conselho ILIKE 'CRM' LIMIT 1")
    ).fetchone()
    conselho_nome = res_conselho[0] if res_conselho else "CRM"

    pagina_atual = req.pagina_inicial
    total_medicos_acumulados = 0
    total_lotes_executados = 0
    tem_mais = True
    total_paginas_portal = 1

    # Loop de lotes se auto_loop_lotes=True, ou lote único se False
    while tem_mais:
        worker_url = f"{BACKEND_WORKER_URL}/api/v1/crm/consulta"
        headers = {
            "Authorization": f"Bearer {BPO_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "uf": uf_upper,
            "nome": req.nome,
            "registro": req.registro,
            "pagina_inicial": pagina_atual,
            "max_paginas": min(req.max_paginas, 49),
        }

        logger.info(
            f"[Hub CRM] Solicitando Lote #{total_lotes_executados + 1} ao Worker | "
            f"pagina_inicial={pagina_atual} | max_paginas={payload['max_paginas']}"
        )

        try:
            resp = requests.post(worker_url, json=payload, headers=headers, timeout=300)
        except requests.RequestException as e:
            logger.error(f"[Hub CRM] Falha de conexão com o Worker: {e}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Não foi possível conectar ao serviço de scraping.",
            )

        if resp.status_code != 200:
            logger.error(f"[Hub CRM] Worker retornou status {resp.status_code}: {resp.text}")
            detail = "Erro ao consultar portal CFM."
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(status_code=resp.status_code, detail=detail)

        data = resp.json()
        medicos_lote = data.get("medicos", [])
        total_paginas_portal = data.get("total_paginas_portal", 1)
        proxima_p = data.get("proxima_pagina")
        tem_mais = data.get("tem_mais_paginas", False) and req.auto_loop_lotes

        logger.info(f"[Hub CRM] Worker retornou {len(medicos_lote)} médicos no lote.")

        if not medicos_lote:
            break

        # Persistir no banco com user_id=NULL (livre para todos)
        for med in medicos_lote:
            nome_med = (med.get("nome") or "").strip().upper()
            crm_num = str(med.get("crm") or "").strip()
            situacao_lower = str(med.get("situacao") or "regular").strip().lower()
            especialidades = med.get("especialidades") or []

            if not nome_med or not crm_num:
                continue

            primeira_area = None
            if especialidades:
                primeira_area = str(especialidades[0]).strip().upper()
                for esp in especialidades:
                    esp_upper = str(esp).strip().upper()
                    if esp_upper:
                        db.execute(
                            text("""
                                INSERT INTO public.areas_atuacao (nome, status)
                                VALUES (:nome, 'ativo')
                                ON CONFLICT (nome) DO NOTHING
                            """),
                            {"nome": esp_upper},
                        )


            existente = db.execute(
                text("""
                    SELECT id FROM public.corpo_clinico
                    WHERE conselho ILIKE :conselho
                      AND registro = :registro
                      AND ("UF" ILIKE :uf OR "UF" IS NULL)
                    LIMIT 1
                """),
                {"conselho": conselho_nome, "registro": crm_num, "uf": uf_upper},
            ).fetchone()

            now_ts = datetime.utcnow()

            if existente:
                db.execute(
                    text("""
                        UPDATE public.corpo_clinico
                        SET nome = :nome,
                            situacao = :situacao,
                            atualizado_crm = :now,
                            "UF" = :uf
                        WHERE id = :id
                    """),
                    {
                        "nome": nome_med,
                        "situacao": situacao_lower,
                        "now": now_ts,
                        "uf": uf_upper,
                        "id": existente[0],
                    },
                )
            else:
                db.execute(
                    text("""
                        INSERT INTO public.corpo_clinico (
                            user_id, id_profissional, nome, conselho, registro, "UF", area,
                            status, tipo_profissional, situacao, atualizado_crm
                        ) VALUES (
                            NULL, NULL, :nome, :conselho, :registro, :uf, :area,
                            'ativo', 'medico', :situacao, :now
                        )
                    """),
                    {
                        "nome": nome_med,
                        "conselho": conselho_nome,
                        "registro": crm_num,
                        "uf": uf_upper,
                        "area": primeira_area,
                        "situacao": situacao_lower,
                        "now": now_ts,
                    },
                )

            total_medicos_acumulados += 1

        db.commit()
        total_lotes_executados += 1

        if not req.auto_loop_lotes or not proxima_p:
            break

        pagina_atual = proxima_p

    # Invalidar cache Redis do recurso profissionais
    try:
        cache.invalidate_tenant(current_user.id)
    except Exception as e:
        logger.warning(f"[Hub CRM] Aviso ao invalidar cache Redis: {e}")

    return {
        "status": "success",
        "total_importados": total_medicos_acumulados,
        "lotes_executados": total_lotes_executados,
        "pagina_inicial": req.pagina_inicial,
        "proxima_pagina": proxima_p if data.get("tem_mais_paginas") else None,
        "total_paginas_portal": total_paginas_portal,
        "tem_mais_paginas": data.get("tem_mais_paginas", False),
    }
