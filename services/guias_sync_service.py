"""
Serviço de sincronização de guias via Bulk Upsert.
Consome JSON retornado pelo worker e insere/atualiza em base_guias via INSERT ON CONFLICT.

Substitui o loop row-by-row do dispatcher por uma única query batch.
"""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from models import BaseGuia


def _parse_date(date_str):
    """Parse date string em múltiplos formatos."""
    if not date_str or not isinstance(date_str, str):
        return None
    clean = date_str.strip()[:10]
    try:
        if "-" in clean:
            return datetime.strptime(clean, "%Y-%m-%d").date()
        return datetime.strptime(clean, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _parse_int(val, default=0):
    """Parse int de forma segura."""
    try:
        clean = str(val).strip()
        if not clean or clean.lower() in ["none", "null", ""]:
            return default
        return int(clean)
    except (ValueError, TypeError):
        return default


def _normalize_status(status_raw, id_convenio: int, item: dict) -> str:
    """Normaliza status da guia considerando mapeamentos por convênio."""
    status = str(status_raw).strip() if status_raw else "Autorizado"
    
    # Mapeamento Bradesco (Orizon)
    if id_convenio == 1:
        if item.get("descricao"):
            return str(item["descricao"]).strip()
        status_map = {
            "4": "Liberada",
            "5": "Exportada",
            "199": "Pendente",
        }
        if status in status_map:
            return status_map[status]
    
    return status


# Status válidos para inserção de novas guias
VALID_STATUS = {
    "AUTORIZADO", "EM ESTUDO", "SOLICITADO", "EM AVALIAÇÃO",
    "EM APROVAÇÃO E AGUARDANDO P", "NEGADO", "CANCELADO",
    "EXPORTADA", "EXPORTADO", "PENDENTE", "FATURADA", "LIBERADA"
}


def bulk_upsert_guias_from_json(
    db: Session,
    results: list,
    id_convenio: int,
    user_id: int,
    carteirinha_id: int = None,
) -> dict:
    """
    Realiza bulk upsert de guias a partir do JSON retornado pelo worker.
    
    Utiliza INSERT ... ON CONFLICT para reduzir N*2 queries para 1.
    
    Args:
        db: Sessão SQLAlchemy
        results: Lista de dicts com dados das guias (JSON do worker)
        id_convenio: ID do convênio
        user_id: ID do usuário (tenant)
        carteirinha_id: ID da carteirinha (pode ser None para jobs sem carteirinha fixa)
    
    Returns:
        dict com contadores: {"total": N, "affected_rows": M, "skipped": K}
    """
    if not results:
        return {"total": 0, "affected_rows": 0, "skipped": 0}
    
    records = []
    skipped = 0
    
    for item in results:
        # Normalizar status
        status_val = _normalize_status(
            item.get("status_guia", item.get("status", "Autorizado")),
            id_convenio, item
        )
        
        guia_num = str(item.get("numero_guia", item.get("guia", ""))).strip()
        if not guia_num:
            skipped += 1
            continue
        
        # Para novos registros, verificar se status é válido
        # (atualizações de registros existentes passam independente do status)
        
        codigo_terapia_val = item.get("codigo_terapia", item.get("codigo_procedimento"))
        
        # Resolver carteirinha_id dinâmico se necessário
        current_cid = carteirinha_id or item.get("carteirinha_id")
        if not current_cid and item.get("codigo_beneficiario"):
            from models import Carteirinha
            cart = db.query(Carteirinha).filter(
                Carteirinha.codigo_beneficiario == item["codigo_beneficiario"],
                Carteirinha.user_id == user_id
            ).first()
            if cart:
                current_cid = cart.id
        
        records.append({
            "id_convenio": id_convenio,
            "carteirinha_id": current_cid,
            "guia": guia_num,
            "guia_prestador": item.get("guia_prestador"),
            "codigo_terapia": codigo_terapia_val,
            "nome_terapia": item.get("nome_terapia"),
            "senha": str(item.get("senha", "")).strip() if item.get("senha") else None,
            "status_guia": status_val,
            "data_solicitacao": _parse_date(item.get("data_solicitacao")),
            "data_autorizacao": _parse_date(item.get("data_autorizacao")),
            "validade": _parse_date(
                item.get("validade_senha", item.get("data_validade", item.get("validade")))
            ),
            "qtde_solicitada": _parse_int(
                item.get("qtde_solicitada", item.get("qtde_solicitado")), 0
            ),
            "sessoes_autorizadas": _parse_int(
                item.get("qtde_autorizada",
                         item.get("sessoes_autorizadas",
                                  item.get("qtde_autorizado"))), 0
            ),
            "codigo_beneficiario": item.get("codigo_beneficiario"),
            "cod_prestador": item.get("cod_prestador"),
            "user_id": user_id,
            "updated_at": datetime.now(timezone.utc),
        })
    
    if not records:
        return {"total": 0, "affected_rows": 0, "skipped": skipped}
    
    # Batch INSERT ON CONFLICT using in-memory mapping to avoid N+1 queries.
    guia_list = list({r["guia"] for r in records})
    convenio_list = list({r["id_convenio"] for r in records})
    
    existing_guias = db.query(BaseGuia).filter(
        BaseGuia.user_id == user_id,
        BaseGuia.guia.in_(guia_list),
        BaseGuia.id_convenio.in_(convenio_list)
    ).all()
    
    existing_map = {}
    for eg in existing_guias:
        norm_guia = str(eg.guia).strip()
        norm_conv = eg.id_convenio
        norm_ter = str(eg.codigo_terapia).strip() if eg.codigo_terapia else ""
        norm_cid = eg.carteirinha_id
        key = (norm_guia, norm_conv, norm_ter, norm_cid)
        existing_map[key] = eg

    count_inserted = 0
    count_updated = 0
    
    for record in records:
        norm_guia = record["guia"]
        norm_conv = record["id_convenio"]
        norm_ter = str(record["codigo_terapia"]).strip() if record["codigo_terapia"] else ""
        norm_cid = record["carteirinha_id"]
        
        key = (norm_guia, norm_conv, norm_ter, norm_cid)
        existing = existing_map.get(key)
        
        if existing:
            # Verify ownership
            if existing.user_id and existing.user_id != user_id:
                skipped += 1
                continue
            
            # Update
            for key_attr in ["senha", "status_guia", "data_autorizacao", "data_solicitacao",
                             "validade", "qtde_solicitada", "sessoes_autorizadas",
                             "nome_terapia", "guia_prestador", "codigo_beneficiario",
                             "cod_prestador"]:
                if record.get(key_attr) is not None:
                    setattr(existing, key_attr, record[key_attr])
            if record.get("codigo_terapia") is not None:
                existing.codigo_terapia = record["codigo_terapia"]
            existing.user_id = user_id
            existing.updated_at = datetime.now(timezone.utc)
            count_updated += 1
        else:
            # Verificar status válido para novos registros
            if record["status_guia"].upper() not in VALID_STATUS:
                skipped += 1
                continue
            
            new_guia = BaseGuia(**{k: v for k, v in record.items() if k != "updated_at"})
            new_guia.created_at = datetime.now(timezone.utc)
            db.add(new_guia)
            existing_map[key] = new_guia
            count_inserted += 1
    
    db.commit()
    
    return {
        "total": len(results),
        "inserted": count_inserted,
        "updated": count_updated,
        "skipped": skipped,
        "affected_rows": count_inserted + count_updated,
    }


def bulk_insert_carteirinhas(
    db: Session,
    records: list[dict],
    user_id: int,
) -> dict:
    """
    Bulk insert de carteirinhas ignorando duplicatas.
    Usa INSERT ... ON CONFLICT DO NOTHING na constraint (carteirinha, user_id).
    
    Args:
        db: Sessão SQLAlchemy
        records: Lista de dicts com dados das carteirinhas
        user_id: ID do usuário (tenant)
    
    Returns:
        dict com contadores
    """
    from models import Carteirinha
    
    if not records:
        return {"total": 0, "inserted": 0, "skipped": 0}
    
    # Normalizar registros
    normalized = []
    for r in records:
        normalized.append({
            "carteirinha": str(r.get("carteirinha", "")).strip(),
            "paciente": str(r.get("paciente", "")).strip() if r.get("paciente") else None,
            "id_paciente": str(r.get("id_paciente", "")).strip() if r.get("id_paciente") else None,
            "codigo_beneficiario": str(r.get("codigo_beneficiario", "")).strip() if r.get("codigo_beneficiario") else None,
            "id_convenio": r.get("id_convenio"),
            "user_id": user_id,
            "status": r.get("status", "ativo"),
        })
    
    # Filtrar registros sem carteirinha
    normalized = [r for r in normalized if r["carteirinha"]]
    
    if not normalized:
        return {"total": 0, "inserted": 0, "skipped": 0}
    
    # INSERT ON CONFLICT DO NOTHING
    stmt = pg_insert(Carteirinha).values(normalized)
    stmt = stmt.on_conflict_do_nothing(
        constraint="uq_carteirinha_user_id"
    )
    
    result = db.execute(stmt)
    db.commit()
    
    inserted = result.rowcount
    return {
        "total": len(normalized),
        "inserted": inserted,
        "skipped": len(normalized) - inserted,
    }


def sync_completed_worker_jobs(db: Session) -> dict:
    """
    Consome resultados de jobs concluídos com sucesso e sincroniza com a base de guias.
    """
    from models import Job
    
    # Busca todos os jobs bem-sucedidos cujo resultado ainda não foi consumido pelo backend principal
    jobs = db.query(Job).filter(
        Job.status == "success",
        Job.result_consumed == False
    ).all()
    
    synced_counts = {"jobs_processed": 0, "inserted": 0, "updated": 0, "skipped": 0}
    
    for job in jobs:
        if not job.result_data:
            job.result_consumed = True
            continue
            
        results_list = []
        if isinstance(job.result_data, dict):
            results_list = job.result_data.get("data", [])
        elif isinstance(job.result_data, list):
            results_list = job.result_data
            
        if results_list:
            res = bulk_upsert_guias_from_json(
                db=db,
                results=results_list,
                id_convenio=job.id_convenio,
                user_id=job.user_id,
                carteirinha_id=job.carteirinha_id
            )
            synced_counts["inserted"] += res.get("inserted", 0)
            synced_counts["updated"] += res.get("updated", 0)
            synced_counts["skipped"] += res.get("skipped", 0)
            
        # Invalidação do cache para o usuário proprietário do job
        try:
            from cache import cache
            cache.invalidate_tenant(job.user_id)
        except Exception as e:
            print(f"Error invalidating cache for user {job.user_id} during sync: {e}")

        job.result_consumed = True
        synced_counts["jobs_processed"] += 1
        
    if synced_counts["jobs_processed"] > 0:
        db.commit()
        
    return synced_counts

