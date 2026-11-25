"""
Serviço da OP20 (IPASGO — convênio 6): Validação e Extração de CodigoBeneficiario.

Garantias de Isolamento Multi-Tenant:
- Toda criação de job filtra estritamente `Carteirinha.user_id == user_id` e `Carteirinha.id_convenio == 6`.
- Todo consumo de resultado valida que cada `carteirinha_id` pertence ao `job.user_id` antes de atualizar.
- Detecção de duplicados de `codigo_beneficiario` ocorre exclusivamente dentro do mesmo `user_id`.
- Preserva o valor exato de `CodigoBeneficiario` retornado pelo portal (ex.: "040473-0").
"""
import json
import os
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import (
    Carteirinha,
    CarteirinhaCodigoBenefAuditoria,
    Job,
    UserConvenio,
)

IPASGO_CONVENIO_ID = 6
OP20_ROTINA = "op20_validar_codigo_beneficiario"
OP20_CHUNK_SIZE = 100


def criar_jobs_validacao_codigo_beneficiario(
    db: Session,
    user_id: int,
    carteirinha_ids: Optional[List[int]] = None,
    modo: str = "validar_e_atualizar",
    apenas_sem_codigo: bool = False,
    confirmar_pesquisar: bool = False,
) -> dict:
    """
    Cria jobs em chunks (~100 carteirinhas por job) para validar/extrair o
    CodigoBeneficiario no portal IPASGO, estritamente isolados ao `user_id`.
    """
    if not user_id:
        raise ValueError("user_id é obrigatório para criar jobs da OP20.")

    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == user_id,
        UserConvenio.id_convenio == IPASGO_CONVENIO_ID,
        UserConvenio.login.isnot(None),
        UserConvenio.senha_criptografada.isnot(None),
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise ValueError(
            "Credenciais do IPASGO não cadastradas para este usuário. "
            "Configure em Credenciais Portais antes de iniciar a OP20."
        )

    query = db.query(Carteirinha).filter(
        Carteirinha.user_id == user_id,
        Carteirinha.id_convenio == IPASGO_CONVENIO_ID,
        Carteirinha.is_temporary == False,
    )

    ids_solicitados = 0
    ids_descartados = 0
    if carteirinha_ids is not None and len(carteirinha_ids) > 0:
        ids_limpos = list({int(cid) for cid in carteirinha_ids if cid is not None})
        ids_solicitados = len(ids_limpos)
        query = query.filter(Carteirinha.id.in_(ids_limpos))

    if apenas_sem_codigo:
        query = query.filter(
            or_(
                Carteirinha.codigo_beneficiario.is_(None),
                func.trim(Carteirinha.codigo_beneficiario) == "",
            )
        )

    carteirinhas = query.order_by(Carteirinha.id.asc()).all()
    if ids_solicitados > 0:
        ids_descartados = max(0, ids_solicitados - len(carteirinhas))

    if not carteirinhas:
        return {
            "jobs_created": 0,
            "total_carteirinhas": 0,
            "ids_descartados_outro_tenant": ids_descartados,
            "job_ids": [],
        }

    # Cancelar eventuais jobs OP20 anteriores travados deste mesmo usuário quando for lote completo
    if not carteirinha_ids:
        jobs_antigos = db.query(Job).filter(
            Job.user_id == user_id,
            Job.id_convenio == IPASGO_CONVENIO_ID,
            Job.rotina.in_(["20", "op20", OP20_ROTINA]),
            Job.status.in_(["pending", "running", "processing"]),
        ).all()
        for j_old in jobs_antigos:
            j_old.status = "error"
            j_old.error_message = "Substituído por nova validação de Código Beneficiário (OP20)"

    modo_norm = (
        "somente_validar"
        if str(modo or "").strip().lower() in ("somente_validar", "validar", "dry_run")
        else "validar_e_atualizar"
    )

    from services.job_service import _enrich_params_with_convenio_config, MY_WEBHOOK_URL

    cod_prestador = (uconv.cod_prestador or "").strip()
    base_params = {
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "cod_prestador": cod_prestador,
        "codigoPrestador": cod_prestador,
        "prestador": cod_prestador,
        "login_fat": uconv.login_fat,
        "senha_fat_criptografada": uconv.senha_fat_criptografada,
        "webhook_url": MY_WEBHOOK_URL,
        "modo": modo_norm,
        "confirmar_pesquisar": bool(confirmar_pesquisar),
    }
    base_params = _enrich_params_with_convenio_config(db, IPASGO_CONVENIO_ID, base_params)

    itens_serializados = [
        {
            "carteirinha_id": c.id,
            "carteirinha": (c.carteirinha or "").strip(),
            "paciente": (c.paciente or "").strip(),
            "codigo_beneficiario": c.codigo_beneficiario,
        }
        for c in carteirinhas
        if (c.carteirinha or "").strip()
    ]

    criados_ids = []
    for i in range(0, len(itens_serializados), OP20_CHUNK_SIZE):
        chunk = itens_serializados[i : i + OP20_CHUNK_SIZE]
        p_chunk = dict(base_params)
        p_chunk["carteirinhas"] = chunk
        p_chunk["chunk_index"] = (i // OP20_CHUNK_SIZE) + 1
        p_chunk["total_chunks"] = (len(itens_serializados) + OP20_CHUNK_SIZE - 1) // OP20_CHUNK_SIZE
        # Se o chunk tiver apenas 1 carteirinha, vincula carteirinha_id no Job também
        cid_job = chunk[0]["carteirinha_id"] if len(chunk) == 1 else None
        if len(chunk) == 1:
            p_chunk["Carteira"] = chunk[0]["carteirinha"]
            p_chunk["carteira"] = chunk[0]["carteirinha"]
            p_chunk["Paciente"] = chunk[0]["paciente"]

        novo_job = Job(
            carteirinha_id=cid_job,
            id_convenio=IPASGO_CONVENIO_ID,
            user_id=user_id,
            rotina=OP20_ROTINA,
            params=p_chunk,
            status="pending",
            priority=0,
            max_attempts=2,
        )
        db.add(novo_job)
        db.flush()
        criados_ids.append(novo_job.id)

    db.commit()
    return {
        "jobs_created": len(criados_ids),
        "total_carteirinhas": len(itens_serializados),
        "ids_descartados_outro_tenant": ids_descartados,
        "modo": modo_norm,
        "job_ids": criados_ids,
    }


def consumir_resultado_op20(db: Session, job: Job, res_data, synced_counts: dict) -> dict:
    """
    Consome o retorno da OP20:
    1. Valida posse multi-tenant (`Carteirinha.user_id == job.user_id`).
    2. Registra linha em `carteirinha_codigo_benef_auditoria`.
    3. Atualiza `codigo_benef_validado_em` e `codigo_benef_status` na carteirinha.
    4. Se `modo == "validar_e_atualizar"` e `status == "OK"`, grava `codigo_beneficiario`
       (preservando o formato exato, ex.: "040473-0") e executa backfill em `base_guias`.
    """
    user_id = job.user_id
    if not user_id:
        job.result_consumed = True
        db.commit()
        return {"updated": 0, "audited": 0, "skipped_tenant": 0}

    params_job = job.params if isinstance(job.params, dict) else {}
    if isinstance(job.params, str):
        try:
            params_job = json.loads(job.params)
        except Exception:
            params_job = {}

    modo = "validar_e_atualizar"
    itens = []
    if isinstance(res_data, dict):
        modo = str(res_data.get("modo") or params_job.get("modo") or "validar_e_atualizar").strip().lower()
        if isinstance(res_data.get("itens"), list):
            itens = res_data["itens"]
        elif isinstance(res_data.get("data"), dict) and isinstance(res_data["data"].get("itens"), list):
            itens = res_data["data"]["itens"]
            modo = str(res_data["data"].get("modo") or modo).strip().lower()
        elif isinstance(res_data.get("data"), list):
            itens = res_data["data"]
    elif isinstance(res_data, list):
        itens = res_data
        modo = str(params_job.get("modo") or "validar_e_atualizar").strip().lower()

    deve_atualizar = modo not in ("somente_validar", "validar", "dry_run")
    now_utc = datetime.now(timezone.utc)

    # Pré-carregar todas as carteirinhas do usuário para o convênio 6
    carts_usuario = db.query(Carteirinha).filter(
        Carteirinha.user_id == user_id,
        Carteirinha.id_convenio == IPASGO_CONVENIO_ID,
    ).all()
    por_id = {c.id: c for c in carts_usuario}
    por_numero = {(c.carteirinha or "").strip(): c for c in carts_usuario if (c.carteirinha or "").strip()}

    updated_count = 0
    audited_count = 0
    skipped_tenant = 0
    pares_backfill = []

    for item in itens:
        if not isinstance(item, dict):
            continue

        cid = item.get("carteirinha_id")
        cart_num = str(item.get("carteirinha") or "").strip()
        status_item = str(item.get("status") or "ERRO").strip().upper()
        codigo_portal = item.get("codigo_portal")
        if codigo_portal is not None:
            # Preservar formato exato (ex: "040473-0"), apenas removendo espaços nas pontas
            codigo_portal = str(codigo_portal).strip() or None

        cart_obj = None
        if cid is not None:
            try:
                cart_obj = por_id.get(int(cid))
            except (ValueError, TypeError):
                cart_obj = None
            # Se informou carteirinha_id que não pertence ao user_id do job, descartar!
            if cart_obj is None:
                skipped_tenant += 1
                print(
                    f"[OP20 TENANT GUARD] Job #{job.id} (user_id={user_id}): "
                    f"carteirinha_id={cid} não pertence ao usuário. Ignorado."
                )
                continue
        elif cart_num:
            cart_obj = por_numero.get(cart_num)
            if cart_obj is None:
                skipped_tenant += 1
                continue
        else:
            continue

        codigo_anterior = cart_obj.codigo_beneficiario
        aplicado = False

        cart_obj.codigo_benef_validado_em = now_utc
        cart_obj.codigo_benef_status = status_item

        if deve_atualizar and status_item == "OK" and codigo_portal:
            if cart_obj.codigo_beneficiario != codigo_portal:
                cart_obj.codigo_beneficiario = codigo_portal
                cart_obj.updated_at = now_utc
                updated_count += 1
                pares_backfill.append((cart_obj.id, codigo_portal, user_id))
            aplicado = True

        auditoria = CarteirinhaCodigoBenefAuditoria(
            user_id=user_id,
            carteirinha_id=cart_obj.id,
            carteirinha=cart_obj.carteirinha,
            paciente=cart_obj.paciente,
            codigo_anterior=codigo_anterior,
            codigo_portal=codigo_portal,
            titulo=item.get("titulo"),
            opcoes=item.get("opcoes") if isinstance(item.get("opcoes"), list) else None,
            fonte=item.get("fonte"),
            status=status_item,
            aplicado=aplicado,
            job_id=job.id,
            erro=item.get("erro"),
        )
        db.add(auditoria)
        audited_count += 1

    job.result_consumed = True
    synced_counts["jobs_processed"] = synced_counts.get("jobs_processed", 0) + 1
    synced_counts["updated"] = synced_counts.get("updated", 0) + updated_count
    db.commit()

    if pares_backfill:
        try:
            from services.guias_sync_service import backfill_carteirinha_em_guias
            backfill_carteirinha_em_guias(db, pares_backfill)
        except Exception as e:
            print(f"[OP20 BACKFILL] Erro ao re-vincular guias para user_id={user_id}: {e}")

    try:
        from cache import cache
        cache.invalidate_tenant(user_id)
    except Exception:
        pass

    return {
        "updated": updated_count,
        "audited": audited_count,
        "skipped_tenant": skipped_tenant,
    }


def listar_duplicados_codigo_beneficiario(db: Session, user_id: int) -> list:
    """
    Detecta carteirinhas do MESMO `user_id` (convênio 6) que compartilham
    o mesmo `codigo_beneficiario`. Nunca cruza dados entre tenants distintos.
    """
    sub = (
        db.query(
            Carteirinha.codigo_beneficiario,
            func.count(Carteirinha.id).label("qtd"),
        )
        .filter(
            Carteirinha.user_id == user_id,
            Carteirinha.id_convenio == IPASGO_CONVENIO_ID,
            Carteirinha.is_temporary == False,
            Carteirinha.codigo_beneficiario.isnot(None),
            func.trim(Carteirinha.codigo_beneficiario) != "",
        )
        .group_by(Carteirinha.codigo_beneficiario)
        .having(func.count(Carteirinha.id) > 1)
        .all()
    )

    codigos_dup = [row[0] for row in sub]
    if not codigos_dup:
        return []

    carts = (
        db.query(Carteirinha)
        .filter(
            Carteirinha.user_id == user_id,
            Carteirinha.id_convenio == IPASGO_CONVENIO_ID,
            Carteirinha.is_temporary == False,
            Carteirinha.codigo_beneficiario.in_(codigos_dup),
        )
        .order_by(Carteirinha.codigo_beneficiario.asc(), Carteirinha.paciente.asc())
        .all()
    )

    agrupado = {}
    for c in carts:
        cod = c.codigo_beneficiario
        agrupado.setdefault(cod, []).append({
            "id": c.id,
            "carteirinha": c.carteirinha,
            "paciente": c.paciente,
            "codigo_beneficiario": c.codigo_beneficiario,
            "status": c.status,
            "codigo_benef_status": c.codigo_benef_status,
            "codigo_benef_validado_em": (
                c.codigo_benef_validado_em.isoformat() if c.codigo_benef_validado_em else None
            ),
        })

    return [
        {
            "codigo_beneficiario": cod,
            "quantidade": len(lista),
            "carteirinhas": lista,
        }
        for cod, lista in agrupado.items()
    ]


def listar_auditoria_codigo_beneficiario(
    db: Session,
    user_id: int,
    status: Optional[str] = None,
    apenas_divergentes: bool = False,
    job_id: Optional[int] = None,
    limit: int = 5000,
) -> list:
    """
    Lista o resultado mais recente de validação da OP20 por carteirinha,
    estritamente filtrado por `user_id`.
    """
    # Subquery com o ID de auditoria mais recente por carteirinha do usuário
    sub_latest = (
        db.query(
            func.max(CarteirinhaCodigoBenefAuditoria.id).label("max_id")
        )
        .filter(CarteirinhaCodigoBenefAuditoria.user_id == user_id)
        .group_by(
            func.coalesce(
                CarteirinhaCodigoBenefAuditoria.carteirinha_id,
                -CarteirinhaCodigoBenefAuditoria.id,
            )
        )
        .subquery()
    )

    q = db.query(CarteirinhaCodigoBenefAuditoria).filter(
        CarteirinhaCodigoBenefAuditoria.user_id == user_id
    )
    if job_id:
        q = q.filter(CarteirinhaCodigoBenefAuditoria.job_id == int(job_id))
    else:
        q = q.filter(CarteirinhaCodigoBenefAuditoria.id.in_(db.query(sub_latest.c.max_id)))

    if status:
        q = q.filter(CarteirinhaCodigoBenefAuditoria.status == status.strip().upper())
    if apenas_divergentes:
        q = q.filter(
            or_(
                CarteirinhaCodigoBenefAuditoria.codigo_anterior.is_(None),
                CarteirinhaCodigoBenefAuditoria.codigo_anterior != CarteirinhaCodigoBenefAuditoria.codigo_portal,
            )
        )

    rows = q.order_by(CarteirinhaCodigoBenefAuditoria.paciente.asc(), CarteirinhaCodigoBenefAuditoria.id.desc()).limit(min(limit, 10000)).all()
    return [
        {
            "id": r.id,
            "carteirinha_id": r.carteirinha_id,
            "carteirinha": r.carteirinha,
            "paciente": r.paciente,
            "codigo_anterior": r.codigo_anterior,
            "codigo_portal": r.codigo_portal,
            "titulo": r.titulo,
            "opcoes": r.opcoes or [],
            "fonte": r.opcoes and r.fonte or r.fonte,
            "status": r.status,
            "aplicado": r.aplicado,
            "job_id": r.job_id,
            "erro": r.erro,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def aplicar_auditoria_codigo_beneficiario(
    db: Session,
    user_id: int,
    auditoria_ids: List[int],
) -> dict:
    """
    Aplica manualmente o `codigo_portal` de registros de auditoria selecionados
    sobre as carteirinhas do próprio `user_id`.
    """
    if not auditoria_ids:
        return {"applied": 0}

    registros = (
        db.query(CarteirinhaCodigoBenefAuditoria)
        .filter(
            CarteirinhaCodigoBenefAuditoria.id.in_(auditoria_ids),
            CarteirinhaCodigoBenefAuditoria.user_id == user_id,
        )
        .all()
    )

    now_utc = datetime.now(timezone.utc)
    applied = 0
    pares_backfill = []

    for reg in registros:
        if not reg.codigo_portal or not reg.carteirinha_id:
            continue
        cart = (
            db.query(Carteirinha)
            .filter(
                Carteirinha.id == reg.carteirinha_id,
                Carteirinha.user_id == user_id,
                Carteirinha.id_convenio == IPASGO_CONVENIO_ID,
            )
            .first()
        )
        if not cart:
            continue
        cart.codigo_beneficiario = reg.codigo_portal
        cart.codigo_benef_validado_em = now_utc
        cart.codigo_benef_status = "OK"
        cart.updated_at = now_utc
        reg.aplicado = True
        applied += 1
        pares_backfill.append((cart.id, reg.codigo_portal, user_id))

    db.commit()

    if pares_backfill:
        try:
            from services.guias_sync_service import backfill_carteirinha_em_guias
            backfill_carteirinha_em_guias(db, pares_backfill)
        except Exception as e:
            print(f"[OP20 APPLY BACKFILL] Erro: {e}")

    try:
        from cache import cache
        cache.invalidate_tenant(user_id)
    except Exception:
        pass

    return {"applied": applied}
