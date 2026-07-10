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


def is_authorized_status(status_val: str, id_convenio: int) -> bool:
    if not status_val:
        return False
    status_lower = str(status_val).lower()
    if id_convenio == 6:  # IPASGO
        return "autorizad" in status_lower
    else:  # Outros convênios
        return "autorizad" in status_lower or "liberad" in status_lower


def bulk_upsert_guias_from_json(
    db: Session,
    results: list,
    id_convenio: int,
    user_id: int,
    carteirinha_id: int = None,
    job_id: int = None,
) -> dict:
    """
    Realiza bulk upsert de guias a partir do JSON retornado pelo worker.
    Separa guias autorizadas/liberadas para base_guias e as pendentes/negadas para solicitacoes.
    """
    if not results:
        return {"total": 0, "affected_rows": 0, "skipped": 0}
    
    # 1. Coletar códigos de beneficiário para lookup em batch
    codigos_benef = set()
    for item in results:
        if isinstance(item, dict) and item.get("codigo_beneficiario"):
            codigos_benef.add(item["codigo_beneficiario"])
            
    carteirinha_map = {}
    if codigos_benef:
        from models import Carteirinha
        carts = db.query(Carteirinha).filter(
            Carteirinha.codigo_beneficiario.in_(list(codigos_benef)),
            Carteirinha.user_id == user_id
        ).all()
        for cart in carts:
            carteirinha_map[cart.codigo_beneficiario] = cart.id
            
    records = []
    skipped = 0
    
    for item in results:
        if not isinstance(item, dict):
            skipped += 1
            continue
        # Normalizar status
        status_val = _normalize_status(
            item.get("status_guia", item.get("status", "Autorizado")),
            id_convenio, item
        )
        
        guia_num = str(item.get("numero_guia", item.get("guia", ""))).strip()
        if not guia_num:
            skipped += 1
            continue
        
        codigo_terapia_val = item.get("codigo_terapia", item.get("codigo_procedimento"))
        
        # Resolver carteirinha_id dinâmico se necessário
        current_cid = carteirinha_id or item.get("carteirinha_id")
        if not current_cid and item.get("codigo_beneficiario"):
            current_cid = carteirinha_map.get(item["codigo_beneficiario"])
        
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
            "user_id": user_id
        })
    
    if not records:
        return {"total": len(results), "inserted": 0, "updated": 0, "skipped": skipped, "affected_rows": 0}
        
    authorized_records = []
    non_authorized_records = []
    
    for r in records:
        if is_authorized_status(r["status_guia"], id_convenio):
            authorized_records.append(r)
        else:
            non_authorized_records.append(r)
            
    count_inserted = 0
    count_updated = 0
    
    # ─── PROCESSAR GUIAS AUTORIZADAS (base_guias) ───
    if authorized_records:
        guia_list = list({r["guia"] for r in authorized_records})
        convenio_list = list({r["id_convenio"] for r in authorized_records})
        
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
            key = (norm_guia, norm_conv, norm_ter)
            if key not in existing_map or (eg.carteirinha_id and not existing_map[key].carteirinha_id):
                existing_map[key] = eg
                
        for record in authorized_records:
            norm_guia = record["guia"]
            norm_conv = record["id_convenio"]
            norm_ter = str(record["codigo_terapia"]).strip() if record["codigo_terapia"] else ""
            
            key = (norm_guia, norm_conv, norm_ter)
            existing = existing_map.get(key)
            
            if existing:
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
                if record.get("carteirinha_id") is not None:
                    existing.carteirinha_id = record["carteirinha_id"]
                existing.user_id = user_id
                existing.updated_at = datetime.now(timezone.utc)
                count_updated += 1
            else:
                new_guia = BaseGuia(**record)
                new_guia.created_at = datetime.now(timezone.utc)
                new_guia.updated_at = datetime.now(timezone.utc)
                db.add(new_guia)
                db.flush()
                existing_map[key] = new_guia
                count_inserted += 1
                
            # Atualizar status em solicitações caso exista e vincular base_guia_id
            from models import Solicitacao
            sol = None
            if job_id:
                sol = db.query(Solicitacao).filter(
                    Solicitacao.job_id == job_id,
                    Solicitacao.user_id == user_id,
                    Solicitacao.codigo_terapia == record["codigo_terapia"],
                    Solicitacao.carteirinha_id == record["carteirinha_id"]
                ).first()

            if not sol:
                sol = db.query(Solicitacao).filter(
                    Solicitacao.guia == norm_guia,
                    Solicitacao.id_convenio == norm_conv,
                    Solicitacao.codigo_terapia == record["codigo_terapia"],
                    Solicitacao.user_id == user_id
                ).first()
                
            if sol:
                conflict_sol = db.query(Solicitacao).filter(
                    Solicitacao.guia == norm_guia,
                    Solicitacao.id_convenio == norm_conv,
                    Solicitacao.codigo_terapia == record["codigo_terapia"],
                    Solicitacao.carteirinha_id == record["carteirinha_id"],
                    Solicitacao.user_id == user_id,
                    Solicitacao.id != sol.id
                ).first()
                
                if conflict_sol:
                    conflict_sol.job_id = job_id or conflict_sol.job_id
                    conflict_sol.status_solicitacao = record["status_guia"]
                    conflict_sol.base_guia_id = existing_map[key].id
                    conflict_sol.sessoes_autorizadas = record["sessoes_autorizadas"]
                    conflict_sol.senha = record["senha"]
                    conflict_sol.validade = record["validade"]
                    conflict_sol.data_autorizacao = record["data_autorizacao"]
                    conflict_sol.updated_at = datetime.now(timezone.utc)
                    db.delete(sol)
                else:
                    sol.guia = norm_guia
                    sol.status_solicitacao = record["status_guia"]
                    sol.base_guia_id = existing_map[key].id
                    sol.sessoes_autorizadas = record["sessoes_autorizadas"]
                    sol.senha = record["senha"]
                    sol.validade = record["validade"]
                    sol.data_autorizacao = record["data_autorizacao"]
                    sol.updated_at = datetime.now(timezone.utc)
                
    # ─── PROCESSAR GUIAS NÃO AUTORIZADAS (solicitacoes) ───
    if non_authorized_records:
        from models import Solicitacao
        guia_list = list({r["guia"] for r in non_authorized_records})
        convenio_list = list({r["id_convenio"] for r in non_authorized_records})
        
        existing_sols = db.query(Solicitacao).filter(
            Solicitacao.user_id == user_id,
            Solicitacao.guia.in_(guia_list),
            Solicitacao.id_convenio.in_(convenio_list)
        ).all()
        
        existing_sol_map = {}
        for es in existing_sols:
            norm_guia = str(es.guia).strip()
            norm_conv = es.id_convenio
            norm_ter = str(es.codigo_terapia).strip() if es.codigo_terapia else ""
            key = (norm_guia, norm_conv, norm_ter)
            existing_sol_map[key] = es
            
        for record in non_authorized_records:
            norm_guia = record["guia"]
            norm_conv = record["id_convenio"]
            norm_ter = str(record["codigo_terapia"]).strip() if record["codigo_terapia"] else ""
            
            key = (norm_guia, norm_conv, norm_ter)
            existing_sol = None
            if job_id:
                existing_sol = db.query(Solicitacao).filter(
                    Solicitacao.job_id == job_id,
                    Solicitacao.user_id == user_id,
                    Solicitacao.codigo_terapia == record["codigo_terapia"],
                    Solicitacao.carteirinha_id == record["carteirinha_id"]
                ).first()

            if not existing_sol:
                existing_sol = existing_sol_map.get(key)
            
            if existing_sol:
                if existing_sol.user_id and existing_sol.user_id != user_id:
                    skipped += 1
                    continue
                
                # Check for unique constraint conflict
                conflict_sol = db.query(Solicitacao).filter(
                    Solicitacao.guia == record["guia"],
                    Solicitacao.id_convenio == record["id_convenio"],
                    Solicitacao.codigo_terapia == record["codigo_terapia"],
                    Solicitacao.carteirinha_id == record["carteirinha_id"],
                    Solicitacao.user_id == user_id,
                    Solicitacao.id != existing_sol.id
                ).first()
                
                if conflict_sol:
                    conflict_sol.job_id = job_id or conflict_sol.job_id
                    for key_attr in ["senha", "validade", "qtde_solicitada", "sessoes_autorizadas",
                                     "nome_terapia", "codigo_beneficiario", "data_solicitacao", "data_autorizacao"]:
                        if record.get(key_attr) is not None:
                            setattr(conflict_sol, key_attr, record[key_attr])
                    conflict_sol.status_solicitacao = record["status_guia"]
                    conflict_sol.updated_at = datetime.now(timezone.utc)
                    db.delete(existing_sol)
                else:
                    # Update
                    existing_sol.guia = record["guia"]
                    for key_attr in ["senha", "validade", "qtde_solicitada", "sessoes_autorizadas",
                                     "nome_terapia", "codigo_beneficiario", "data_solicitacao", "data_autorizacao"]:
                        if record.get(key_attr) is not None:
                            setattr(existing_sol, key_attr, record[key_attr])
                    existing_sol.status_solicitacao = record["status_guia"]
                    if record.get("carteirinha_id") is not None:
                        existing_sol.carteirinha_id = record["carteirinha_id"]
                    existing_sol.user_id = user_id
                    existing_sol.updated_at = datetime.now(timezone.utc)
            else:
                # Criar nova solicitação
                new_sol = Solicitacao(
                    user_id=user_id,
                    carteirinha_id=record["carteirinha_id"],
                    id_convenio=record["id_convenio"],
                    guia=record["guia"],
                    codigo_terapia=record["codigo_terapia"],
                    nome_terapia=record["nome_terapia"],
                    qtde_solicitada=record["qtde_solicitada"],
                    sessoes_autorizadas=record["sessoes_autorizadas"],
                    data_solicitacao=record["data_solicitacao"],
                    data_autorizacao=record["data_autorizacao"],
                    senha=record["senha"],
                    validade=record["validade"],
                    status_solicitacao=record["status_guia"],
                    job_id=job_id
                )
                db.add(new_sol)
                db.flush()
                existing_sol_map[key] = new_sol
                
            # Remover de base_guias se existir lá por engano
            existing_base = db.query(BaseGuia).filter(
                BaseGuia.guia == norm_guia,
                BaseGuia.id_convenio == norm_conv,
                BaseGuia.codigo_terapia == record["codigo_terapia"],
                BaseGuia.user_id == user_id
            ).first()
            if existing_base:
                db.delete(existing_base)
                
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

        # Se for convênio Evoluir (ID 100)
        if job.id_convenio == 100:
            import json
            rotina = str(job.rotina).lower()
            results_list = []
            if isinstance(job.result_data, list):
                results_list = job.result_data
            elif isinstance(job.result_data, dict):
                data_payload = job.result_data.get("data")
                if isinstance(data_payload, list):
                    results_list = data_payload
                elif isinstance(data_payload, dict):
                    results_list = [data_payload]
                else:
                    results_list = [job.result_data]
            
            if results_list:
                if "op1" in rotina or rotina == "1":
                    import unicodedata
                    def normalize_name(name):
                        if not name:
                            return ""
                        name = name.upper().strip()
                        name = "".join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
                        return " ".join(name.split())
                    
                    from models import Carteirinha, Job as JobModel
                    for p in results_list:
                        id_pac = str(p.get("id_paciente", "")).strip()
                        raw_nome = p.get("paciente", "")
                        if not id_pac or not raw_nome:
                            continue
                        
                        nome_norm = normalize_name(raw_nome)
                        
                        existing = db.query(Carteirinha).filter(
                            Carteirinha.carteirinha == id_pac,
                            Carteirinha.user_id == job.user_id
                        ).first()
                        
                        is_new = False
                        if not existing:
                            existing = Carteirinha(
                                carteirinha=id_pac,
                                paciente=nome_norm,
                                id_paciente=id_pac,
                                id_convenio=6, # IPASGO
                                user_id=job.user_id,
                                status="ativo"
                            )
                            db.add(existing)
                            db.flush()
                            is_new = True
                        else:
                            if existing.paciente != nome_norm:
                                existing.paciente = nome_norm
                        
                        if is_new:
                            op2_params = json.dumps({"id_paciente": id_pac})
                            op2_job = JobModel(
                                status="pending",
                                id_convenio=100,
                                user_id=job.user_id,
                                rotina="op2_obterDetalhes",
                                params=op2_params,
                                priority=0
                            )
                            db.add(op2_job)
                            
                            op3_params = json.dumps({"id_paciente": id_pac, "nome_paciente": nome_norm})
                            op3_job = JobModel(
                                status="pending",
                                id_convenio=100,
                                user_id=job.user_id,
                                rotina="op3_ListarPTS",
                                params=op3_params,
                                priority=0
                            )
                            db.add(op3_job)
                            
                elif "op2" in rotina or rotina == "2":
                    from models import Carteirinha
                    for item in results_list:
                        id_pac = str(item.get("id_paciente", "")).strip()
                        cart_num = str(item.get("carteirinha", "")).strip()
                        cid_val = str(item.get("cid") or item.get("patologia") or "").strip()
                        if not cid_val:
                            cid_val = None
                        
                        if not id_pac:
                            continue
                            
                        cart = db.query(Carteirinha).filter(
                            Carteirinha.id_paciente == id_pac,
                            Carteirinha.user_id == job.user_id
                        ).first()
                        
                        if cart:
                            if cart_num:
                                cart.codigo_beneficiario = cart_num
                            if cid_val:
                                cart.cid = cid_val
                                
                elif "op3" in rotina or rotina == "3":
                    from models import RelatorioClinico
                    
                    # Resolve patient ID fallback from job params
                    job_id_paciente = None
                    try:
                        p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                        job_id_paciente = p_params.get("id_paciente")
                    except Exception:
                        pass
                    if not job_id_paciente and job.carteirinha_id:
                        from models import Carteirinha
                        cart = db.query(Carteirinha).filter(Carteirinha.id == job.carteirinha_id).first()
                        if cart:
                            job_id_paciente = cart.id_paciente

                    for r in results_list:
                        id_rel = str(r.get("id_relatorio", "")).strip()
                        if not id_rel:
                            continue
                            
                        id_pac_val = r.get("id_paciente") or job_id_paciente
                        t_rel = r.get("tipo_relatorio", "PTS")
                        
                        existing_rel = db.query(RelatorioClinico).filter(
                            RelatorioClinico.id_paciente == id_pac_val,
                            RelatorioClinico.id_relatorio == id_rel,
                            RelatorioClinico.tipo_relatorio == t_rel,
                            RelatorioClinico.user_id == job.user_id
                        ).first()
                        
                        data_rel = None
                        if r.get("data"):
                            try:
                                data_rel = datetime.strptime(str(r["data"]).strip()[:10], "%Y-%m-%d").date()
                            except Exception:
                                pass
                        
                        if not existing_rel:
                            new_rel = RelatorioClinico(
                                user_id=job.user_id,
                                id_paciente=id_pac_val,
                                nome_paciente=r.get("nome_paciente"),
                                tipo_relatorio=t_rel,
                                id_relatorio=id_rel,
                                url_arquivo=r.get("url_arquivo"),
                                carga=str(r.get("carga")) if r.get("carga") is not None else None,
                                tipo_carga_horaria=r.get("tipo_carga_horaria"),
                                id_area=r.get("id_area"),
                                data=data_rel,
                                nome_profissional=r.get("nome_profissional")
                            )
                            db.add(new_rel)
                        else:
                            existing_rel.nome_paciente = r.get("nome_paciente")
                            existing_rel.url_arquivo = r.get("url_arquivo")
                            existing_rel.carga = str(r.get("carga")) if r.get("carga") is not None else None
                            existing_rel.tipo_carga_horaria = r.get("tipo_carga_horaria")
                            existing_rel.id_area = r.get("id_area")
                            existing_rel.nome_profissional = r.get("nome_profissional")
                            if data_rel:
                                existing_rel.data = data_rel
                                
                elif "op5" in rotina or rotina == "5":
                    from models import CorpoClinico
                    for p in results_list:
                        id_prof = str(p.get("id_profissional", "")).strip()
                        nome_prof = p.get("nome_profissional")
                        cpf_val = p.get("cpf")
                        registro_val = p.get("registro")
                        specs = p.get("especialidades") or []
                        
                        if not id_prof or not nome_prof:
                            continue
                            
                        if not specs:
                            specs = ['']
                            
                        for spec in specs:
                            spec_clean = str(spec).strip()
                            spec_lower = spec_clean.lower()
                            
                            conselho_val = None
                            if "fisioterapia" in spec_lower or "terapia ocupacional" in spec_lower or "terapeuta ocupacional" in spec_lower:
                                conselho_val = "CREFITO"
                            elif "fonoaudiologia" in spec_lower:
                                conselho_val = "CREFONO"
                            elif "psicologia" in spec_lower:
                                conselho_val = "CRP"
                            elif "psicopedagogia" in spec_lower:
                                has_crp = db.query(CorpoClinico).filter(
                                    CorpoClinico.id_profissional == id_prof,
                                    CorpoClinico.conselho == "CRP"
                                ).first() is not None
                                conselho_val = "CRP" if has_crp else "ABPP"
                            elif "psicomotricista" in spec_lower or "psicomotricidade" in spec_lower:
                                has_crefito = db.query(CorpoClinico).filter(
                                    CorpoClinico.id_profissional == id_prof,
                                    CorpoClinico.conselho == "CREFITO"
                                ).first() is not None
                                conselho_val = "CREFITO" if has_crefito else "CREF"
                            elif "musicoterapia" in spec_lower:
                                conselho_val = "AGMT"
                                
                            cbo_val = None
                            if "fisioterapia" in spec_lower:
                                cbo_val = "223605"
                            elif "terapia ocupacional" in spec_lower or "terapeuta ocupacional" in spec_lower:
                                cbo_val = "223905"
                            elif "fonoaudiologia" in spec_lower:
                                cbo_val = "223810"
                            elif "psicologia" in spec_lower:
                                cbo_val = "251510"
                            elif "psicopedagogia" in spec_lower:
                                cbo_val = "239425"
                            elif "psicomotricista" in spec_lower or "psicomotricidade" in spec_lower:
                                cbo_val = "223910"
                            elif "musicoterapia" in spec_lower:
                                cbo_val = "223915"
                                
                            existing_prof = db.query(CorpoClinico).filter(
                                CorpoClinico.id_profissional == id_prof,
                                CorpoClinico.area == spec_clean
                            ).first()
                            
                            if existing_prof:
                                existing_prof.nome = nome_prof
                                if cpf_val:
                                    existing_prof.cpf = cpf_val
                                if registro_val:
                                    existing_prof.registro = registro_val
                                if conselho_val:
                                    existing_prof.conselho = conselho_val
                                if cbo_val:
                                    existing_prof.CBO = cbo_val
                                existing_prof.status = "ativo"
                                existing_prof.tipo_profissional = "profissional"
                                existing_prof.user_id = 14
                                synced_counts["updated"] += 1
                            else:
                                if not conselho_val or not cbo_val:
                                    any_prof_record = db.query(CorpoClinico).filter(
                                        CorpoClinico.id_profissional == id_prof
                                    ).first()
                                    if any_prof_record:
                                        if not conselho_val:
                                            conselho_val = any_prof_record.conselho
                                        if not cbo_val:
                                            cbo_val = any_prof_record.CBO
                                            
                                new_prof = CorpoClinico(
                                    id_profissional=id_prof,
                                    nome=nome_prof,
                                    cpf=cpf_val,
                                    area=spec_clean,
                                    conselho=conselho_val,
                                    registro=registro_val,
                                    UF="GO",
                                    CBO=cbo_val,
                                    status="ativo",
                                    tipo_profissional="profissional",
                                    user_id=14
                                )
                                db.add(new_prof)
                                synced_counts["inserted"] += 1
                                
            job.result_consumed = True
            synced_counts["jobs_processed"] += 1
            db.commit()
            continue

        results_list = []
        if isinstance(job.result_data, dict):
            # Se for dicionário, pode ter a chave 'data' ou 'op11_data' que contém a lista
            data_payload = job.result_data.get("data", {})
            if isinstance(data_payload, dict):
                if "op11_data" in data_payload and isinstance(data_payload["op11_data"], list):
                    results_list = data_payload["op11_data"]
                elif "data" in data_payload and isinstance(data_payload["data"], list):
                    results_list = data_payload["data"]
                else:
                    results_list = [data_payload]
            elif isinstance(data_payload, list):
                results_list = data_payload
            else:
                results_list = [job.result_data]
        elif isinstance(job.result_data, list):
            results_list = job.result_data
            
        if results_list:
            res = bulk_upsert_guias_from_json(
                db=db,
                results=results_list,
                id_convenio=job.id_convenio,
                user_id=job.user_id,
                carteirinha_id=job.carteirinha_id,
                job_id=job.id
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

def sync_completed_worker_jobs_bg():
    """
    Executa a sincronização em background usando uma nova sessão do banco de dados.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        sync_completed_worker_jobs(db)
    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        print(f"Error in background sync: {e}")
    finally:
        db.close()

