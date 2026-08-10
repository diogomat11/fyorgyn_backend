"""
Serviço de sincronização de guias via Bulk Upsert.
Consome JSON retornado pelo worker e insere/atualiza em base_guias via INSERT ON CONFLICT.

Substitui o loop row-by-row do dispatcher por uma única query batch.
"""
import json
from datetime import datetime, timezone, timedelta, date

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from models import BaseGuia, Log


def _persist_valida_prestador(db: Session, guia_numero: str, codigo_procedimento: str, valida_json: dict) -> int:
    """
    Persiste o JSON `valida_prestador` em public.base_guias para a guia identificada
    por (guia_numero, codigo_procedimento).

    Casamento: prioriza `guia` (numero principal); fallback para `guia_prestador`.
    Filtra tambem por `codigo_terapia` quando informado, para ambiguidade entre
    guias com mesmo numero e terapias diferentes.

    Retorna o numero de linhas atualizadas. Em caso de erro, registra Log WARN
    e retorna 0 (nunca propaga excecao para nao bloquear o consumo do job).

    Estrutura esperada de valida_json (valida_prestador_replication_prompt.yaml):
        {"tipo_json": "All Sucess"|"Thered"|"Null", "guias": {...}}
    ou forma simplificada por guia:
        {"Vinculo_prestador": "Guia Válida"|"...", "codigo_procedimento": "..."}
    """
    if not guia_numero:
        return 0
    try:
        q = db.query(BaseGuia).filter(
            (BaseGuia.guia == str(guia_numero)) | (BaseGuia.guia_prestador == str(guia_numero))
        )
        if codigo_procedimento:
            q = q.filter(BaseGuia.codigo_terapia == str(codigo_procedimento))

        rows = q.all()
        updated = 0
        for row in rows:
            row.valida_prestador = valida_json
            updated += 1
        if updated:
            db.commit()
        else:
            # Nao casou nenhuma guia: registra warning para investigacao manual.
            try:
                db.add(Log(
                    level="WARN",
                    message=(f"[valida_prestador] Guia {guia_numero} (proc={codigo_procedimento}) "
                             "nao casou com nenhuma linha de base_guias para persistir validacao.")
                ))
                db.commit()
            except Exception:
                db.rollback()
        return updated
    except Exception as e:
        try:
            db.rollback()
            db.add(Log(
                level="ERROR",
                message=f"[valida_prestador] Erro ao persistir para guia {guia_numero}: {e}"
            ))
            db.commit()
        except Exception:
            pass
        return 0


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


def _trigger_next_workflow_node(db: Session, job):
    """Verifica se há um fluxo de workflow encadeado configurado em user_convenio_workflows.
    Dada a conclusão do nó atual, busca todos os nós filhos vinculados (em paralelo se for grafo)
    e enfileira automaticamente aqueles configurados como modo_execucao=='automatico'."""
    if not job or not job.user_id or not job.id_convenio:
        return

    from models import UserConvenioWorkflow, Job as JobModel

    wf = db.query(UserConvenioWorkflow).filter(
        UserConvenioWorkflow.user_id == job.user_id,
        UserConvenioWorkflow.id_convenio == job.id_convenio
    ).first()

    if not wf or not wf.fluxo_passos:
        return

    passos = wf.fluxo_passos
    current_rotina = str(job.rotina).lower()

    # Encontra o nó atual no grafo pelo código de rotina ou id
    current_node = None
    current_idx = -1
    for i, p in enumerate(passos):
        cod = str(p.get("codigo_rotina", p.get("acao", ""))).lower()
        if cod == current_rotina or (current_rotina in cod):
            current_node = p
            current_idx = i
            break

    if not current_node and current_idx == -1:
        return

    # Determina os próximos nós no grafo
    next_passos = []
    
    # Formato Grafo / Mapa Mental (next_nodes list)
    next_node_ids = current_node.get("next_nodes") if current_node else None
    if not next_node_ids and current_node:
        next_node_ids = current_node.get("next_node_ids")

    if next_node_ids and isinstance(next_node_ids, list):
        for n_id in next_node_ids:
            found_child = next((p for p in passos if p.get("id") == n_id or p.get("step_id") == n_id), None)
            if found_child:
                next_passos.append(found_child)

    # Fallback Sequencial (se não houver next_nodes explícito)
    if not next_passos and current_idx != -1 and current_idx + 1 < len(passos):
        next_passos.append(passos[current_idx + 1])

    params_data = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})

    # Dispara cada nó filho em paralelo caso o modo de execução seja automático
    for next_p in next_passos:
        if next_p.get("modo_execucao") == "automatico":
            next_rotina = next_p.get("codigo_rotina") or next_p.get("acao")
            if not next_rotina:
                continue

            existing_next = db.query(JobModel).filter(
                JobModel.user_id == job.user_id,
                JobModel.id_convenio == job.id_convenio,
                JobModel.rotina == next_rotina,
                JobModel.status.in_(["pending", "processing"])
            ).first()

            if not existing_next:
                new_next_job = JobModel(
                    user_id=job.user_id,
                    id_convenio=job.id_convenio,
                    rotina=next_rotina,
                    status="pending",
                    params=json.dumps(params_data),
                    priority=0
                )
                db.add(new_next_job)
                print(f"[WORKFLOW GRAPH CHAIN] Nó ramificado '{next_p.get('nome_passo', next_p.get('nome'))}' ({next_rotina}) enfileirado em paralelo com sucesso!")


def _unlink_guia_if_eligible(db: Session, agendamento):
    """
    Desvincula a guia do agendamento se ele receber status 'Falta' ou 'Excluído',
    desde que o lote do agendamento NÃO esteja com status 'Enviado' ou 'Fechado'.
    Incrementa o saldo na tabela base_guias.
    """
    if not agendamento or not agendamento.numero_guia:
        return

    # Verifica se o lote está enviado ou fechado
    lote_status = str(getattr(agendamento, 'status_faturamento', '') or getattr(agendamento, 'status_lote', '') or "").lower().strip()
    if lote_status in ["enviado", "fechado"]:
        # Não desvincula se o lote já tiver sido fechado ou enviado
        return

    numero_guia = agendamento.numero_guia
    agendamento.numero_guia = None

    from models import BaseGuia
    base_guia = db.query(BaseGuia).filter(
        BaseGuia.guia == numero_guia,
        BaseGuia.user_id == agendamento.user_id
    ).first()

    if base_guia:
        base_guia.saldo = (base_guia.saldo or 0) + 1


def _normalize_status(status_raw, id_convenio: int, item: dict) -> str:
    """Normaliza status da guia considerando mapeamentos por convênio e converte termos liberado -> autorizado."""
    status = str(status_raw).strip() if status_raw else "Autorizado"
    
    # Mapeamento Bradesco (Orizon) - ID 1: Liberada / Exportada são apenas para faturamento e não entram na tabela Guias
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
        if status.lower() in ["liberada", "exportada"]:
            return status

    status_lower = status.lower()
    # Converte termos semelhantes a Liberado/Liberada para os padrões Autorizado/Autorizada/Parcialmente autorizada
    if "parcialmente" in status_lower and ("liberad" in status_lower or "autorizad" in status_lower):
        return "Parcialmente autorizada"
    elif "liberada" in status_lower or status_lower == "liberado":
        return "Autorizada" if (status.endswith("a") or "liberada" in status_lower) else "Autorizado"
    elif "autorizada" in status_lower:
        return "Autorizada"
    elif "autorizado" in status_lower:
        return "Autorizado"
    
    return status



# Status válidos para inserção de novas guias
VALID_STATUS = {
    "AUTORIZADO", "AUTORIZADA", "PARCIALMENTE AUTORIZADA",
    "EM ESTUDO", "SOLICITADO", "EM AVALIAÇÃO",
    "EM APROVAÇÃO E AGUARDANDO P", "NEGADO", "CANCELADO",
    "EXPORTADA", "EXPORTADO", "PENDENTE", "FATURADA", "LIBERADA"
}


def is_authorized_status(status_val: str, id_convenio: int) -> bool:
    """
    Verifica se o status normalizado é estritamente autorizado (Autorizado, Autorizada, Parcialmente autorizada).
    Apenas status contendo 'autorizad' são gravados na tabela base_guias.
    Status como Liberada/Exportada do Bradesco Faturamento retornam False e vão para a aba Solicitações.
    """
    if not status_val:
        return False
    status_lower = str(status_val).lower()
    return "autorizad" in status_lower



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

        # Parse timestamp_captura
        ts_captura_raw = item.get("timestamp_captura")
        ts_captura_val = None
        if ts_captura_raw:
            if isinstance(ts_captura_raw, datetime):

                ts_captura_val = ts_captura_raw
            else:
                for fmt in ["%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"]:
                    try:
                        ts_captura_val = datetime.strptime(str(ts_captura_raw).strip(), fmt)
                        break
                    except ValueError:
                        continue

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
            "timestamp_captura": ts_captura_val
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
                                 "cod_prestador", "timestamp_captura"]:
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
    try:
        from cache import cache
        cache.invalidate_tenant(user_id)
    except Exception as cache_err:
        print(f"Error invalidating cache for user {user_id} in bulk_upsert_guias_from_json: {cache_err}")

    
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


def _extract_results_list(result_data):
    if not result_data:
        return []
    if isinstance(result_data, list):
        return result_data
    if isinstance(result_data, dict):
        d1 = result_data.get("data")
        if isinstance(d1, list):
            return d1
        if isinstance(d1, dict):
            d2 = d1.get("data")
            if isinstance(d2, list):
                return d2
            return [d1]
        return [result_data]
    return []


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
            rotina = str(job.rotina).lower()
            results_list = _extract_results_list(job.result_data)
            
            if results_list:
                if "op1" in rotina or rotina == "1":
                    import unicodedata
                    from models import Carteirinha, Job as JobModel, Convenio

                    def normalize_name(name):
                        if not name:
                            return ""
                        name = name.upper().strip()
                        name = "".join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
                        return " ".join(name.split())

                    # Cache local por execução para evitar queries repetidas
                    _conv_cache = {}

                    def _resolve_convenio_id(nome_plano_raw):
                        """Busca convênio por nome EXATO na tabela convenios.
                        Se não encontrado, insere novo usando próximo ID da sequência.
                        Retorna id_convenio."""
                        if not nome_plano_raw:
                            return 6  # fallback IPASGO
                        nome_plano = nome_plano_raw.strip()
                        if nome_plano in _conv_cache:
                            return _conv_cache[nome_plano]
                        conv = db.query(Convenio).filter(
                            Convenio.nome == nome_plano
                        ).first()
                        if conv:
                            _conv_cache[nome_plano] = conv.id_convenio
                            return conv.id_convenio
                        # Não encontrado — inserir novo convênio com próximo ID da sequência
                        from sqlalchemy import text as sa_text
                        next_id = db.execute(sa_text("SELECT nextval('convenios_id_convenio_seq')")).scalar()
                        novo_conv = Convenio(
                            id_convenio=next_id,
                            nome=nome_plano,
                            ativo=True
                        )
                        db.add(novo_conv)
                        db.flush()
                        _conv_cache[nome_plano] = next_id
                        return next_id

                    for p in results_list:
                        id_pac = str(p.get("id_paciente", "")).strip()
                        raw_nome = p.get("paciente", "")
                        nome_plano = p.get("plano", "").strip()
                        if not id_pac or not raw_nome:
                            continue
                        
                        nome_norm = normalize_name(raw_nome)
                        id_conv_item = _resolve_convenio_id(nome_plano)
                        
                        # Buscar por id_paciente primeiro (escopado ao user_id)
                        existing = db.query(Carteirinha).filter(
                            Carteirinha.id_paciente == id_pac,
                            Carteirinha.user_id == job.user_id
                        ).first()
                        
                        # Fallback: buscar por paciente + convênio + user_id
                        if not existing:
                            existing = db.query(Carteirinha).filter(
                                Carteirinha.paciente == nome_norm,
                                Carteirinha.id_convenio == id_conv_item,
                                Carteirinha.user_id == job.user_id
                            ).first()
                            if existing and not existing.id_paciente:
                                existing.id_paciente = id_pac
                        
                        is_new = False
                        if not existing:
                            existing = Carteirinha(
                                carteirinha=None,  # Será preenchido pelo op2 com o número real do cartão de convênio
                                paciente=nome_norm,
                                id_paciente=id_pac,
                                id_convenio=id_conv_item,
                                user_id=job.user_id,
                                status="ativo"
                            )
                            db.add(existing)
                            db.flush()
                            is_new = True
                        else:
                            # Atualizar nome e convênio se necessário
                            if existing.paciente != nome_norm:
                                existing.paciente = nome_norm
                            if not existing.id_paciente:
                                existing.id_paciente = id_pac
                            if nome_plano and existing.id_convenio != id_conv_item:
                                existing.id_convenio = id_conv_item
                        
                        if is_new:
                            # Passar id_convenio_pac para o op2 identificar o convênio correto no webhook
                            op2_params = json.dumps({
                                "id_paciente": id_pac,
                                "id_convenio_pac": id_conv_item,
                                "nome_plano": nome_plano
                            })
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
                    from models import Carteirinha, BaseGuia, Job as JobModel
                    # Recuperar id_convenio_pac dos params do job (injetado pelo op1)
                    try:
                        job_params_raw = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                    except Exception:
                        job_params_raw = {}
                    id_conv_from_job = job_params_raw.get("id_convenio_pac")

                    for item in results_list:
                        id_pac = str(item.get("id_paciente", "")).strip()
                        raw_cart_num = item.get("carteirinha")
                        cart_num = str(raw_cart_num).strip() if raw_cart_num is not None else ""
                        # Ignorar se cart_num for dict cru em formato texto, None ou placeholder
                        if cart_num.startswith('{') or cart_num.startswith('EVOLUIR_') or cart_num.lower() == 'none':
                            cart_num = ""

                        cid_val = str(item.get("cid") or item.get("patologia") or "").strip()
                        if not cid_val or cid_val.lower() == 'none':
                            cid_val = None
                        
                        if not id_pac:
                            continue
                            
                        cart = db.query(Carteirinha).filter(
                            Carteirinha.id_paciente == id_pac,
                            Carteirinha.user_id == job.user_id
                        ).first()
                        
                        if cart:
                            # Atualizar id_convenio se o op1 passou via job params
                            if id_conv_from_job and cart.id_convenio != int(id_conv_from_job):
                                cart.id_convenio = int(id_conv_from_job)

                            if cart_num:
                                cart.codigo_beneficiario = cart_num
                                
                                # Verificar se já existe outra carteirinha com esse cart_num para este mesmo user_id
                                other_cart = db.query(Carteirinha).filter(
                                    Carteirinha.carteirinha == cart_num,
                                    Carteirinha.user_id == job.user_id,
                                    Carteirinha.id != cart.id
                                ).first()
                                
                                if other_cart:
                                    # Consolidar: mesclar id_paciente/cid no registro já existente com carteirinha real
                                    if not other_cart.id_paciente:
                                        other_cart.id_paciente = id_pac
                                    if id_conv_from_job and not other_cart.id_convenio:
                                        other_cart.id_convenio = int(id_conv_from_job)
                                    if cid_val and not other_cart.cid:
                                        other_cart.cid = cid_val
                                    other_cart.codigo_beneficiario = cart_num
                                    
                                    db.query(JobModel).filter(JobModel.carteirinha_id == cart.id).update({JobModel.carteirinha_id: other_cart.id}, synchronize_session=False)
                                    db.query(BaseGuia).filter(BaseGuia.carteirinha_id == cart.id).update({BaseGuia.carteirinha_id: other_cart.id}, synchronize_session=False)
                                    
                                    db.delete(cart)
                                    cart = None
                                else:
                                    cart.carteirinha = cart_num
                            
                            if cid_val and cart is not None:
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
                elif "op7" in rotina or "consultadocs" in rotina:
                    # OP_consultaDocs gera arquivo Excel e insere excel_url no result_data do Job.
                    pass
                                
            job.result_consumed = True
            synced_counts["jobs_processed"] += 1
            db.commit()
            
            # Invalidar cache Redis e local do tenant para que as carteirinhas atualizadas fiquem visíveis imediatamente
            try:
                from cache import cache
                cache.invalidate_tenant(job.user_id)
            except Exception as _ce:
                pass
            continue

        # Se for convênio ABA_clmf (ID 101)
        if job.id_convenio == 101:
            rotina = str(job.rotina).lower()
            results_list = _extract_results_list(job.result_data)

            if "op1" in rotina or "importar_agendamentos" in rotina or rotina == "1":
                _sync_aba_clmf_op1(db, job, results_list, synced_counts)
            elif "op2" in rotina or "consultar_carteirinha" in rotina or rotina == "2":
                _sync_aba_clmf_op2(db, job, results_list, synced_counts)
            elif "op3" in rotina or "confirmar_agendamento" in rotina or rotina == "3":
                p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                ids = p_params.get("id_agendamento", [])
                if isinstance(ids, (int, str)): ids = [int(ids)]
                else: ids = [int(i) for i in ids]
                num_situacao = p_params.get("num_situacao", 1)
                new_status = "Confirmado" if num_situacao == 1 else "A Confirmar"
                if ids:
                    from models import Agendamento, UserConvenio, Job as JobModel
                    db.query(Agendamento).filter(Agendamento.id_agendamento.in_(ids)).update({
                        Agendamento.Status: new_status,
                        Agendamento.execucao_status: "concluido"
                    }, synchronize_session=False)

                    # Automated Workflow Pipeline Chain: If auto_executar is enabled for user+convenio, auto-enqueue execution
                    if num_situacao == 1:
                        uc = db.query(UserConvenio).filter(
                            UserConvenio.user_id == job.user_id,
                            UserConvenio.id_convenio == 101
                        ).first()
                        if uc and uc.auto_executar:
                            for ag_id in ids:
                                exec_job = JobModel(
                                    status="pending",
                                    id_convenio=101,
                                    user_id=job.user_id,
                                    rotina="op3_execucao",
                                    params=json.dumps({"agendamento_id": ag_id}),
                                    priority=0
                                )
                                db.add(exec_job)

            elif "op4" in rotina or "registrar_falta" in rotina or rotina == "4":
                p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                ids = p_params.get("id_agendamento", [])
                if isinstance(ids, (int, str)): ids = [int(ids)]
                else: ids = [int(i) for i in ids]
                if ids:
                    from models import Agendamento
                    falta_ags = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(ids)).all()
                    for ag in falta_ags:
                        ag.Status = "Falta"
                        ag.execucao_status = "concluido"
                        _unlink_guia_if_eligible(db, ag)
            elif "op5" in rotina or "remover_falta" in rotina or rotina == "5":
                p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                ids = p_params.get("id_agendamento", [])
                if isinstance(ids, (int, str)): ids = [int(ids)]
                else: ids = [int(i) for i in ids]
                if ids:
                    from models import Agendamento
                    db.query(Agendamento).filter(Agendamento.id_agendamento.in_(ids)).update({
                        Agendamento.Status: "A Confirmar",
                        Agendamento.execucao_status: "concluido"
                    }, synchronize_session=False)

            elif "op6" in rotina or "atualizar_rc" in rotina or rotina == "6":
                # OP6 ABA_CLMF (101): Atualizar Relatorio Clinico Mensal + baixar PDF.
                # Resultado vem em result_data como {status, op, paciente, id_paciente,
                # data_RC, pdf_caminho, pdf_nome}. O Hub apenas registra evento/log;
                # persistencia adicional (tabela de RCs) fica para versao futura.
                try:
                    rd = job.result_data if isinstance(job.result_data, dict) else {}
                    pdf_caminho = rd.get("pdf_caminho") or rd.get("pdf_path") or ""
                    paciente = rd.get("paciente", "")
                    if str(rd.get("status", "")).lower() == "success":
                        db.add(Log(
                            job_id=job.id,
                            user_id=job.user_id,
                            level="INFO",
                            message=f"[ABA_clmf OP6] RC atualizado para paciente='{paciente}'. PDF: {pdf_caminho}"
                        ))
                    else:
                        db.add(Log(
                            job_id=job.id,
                            user_id=job.user_id,
                            level="WARN",
                            message=f"[ABA_clmf OP6] Falha relatada pelo worker: {rd.get('message', '')} (code={rd.get('code', '')})"
                        ))
                except Exception as log_e:
                    # Log de falha de log nao deve impedir o consumo do job.
                    try:
                        db.add(Log(
                            job_id=job.id,
                            user_id=job.user_id,
                            level="ERROR",
                            message=f"[ABA_clmf OP6] Erro ao registrar log de conclusao: {log_e}"
                        ))
                    except Exception:
                        pass

            _trigger_next_workflow_node(db, job)
            job.result_consumed = True
            synced_counts["jobs_processed"] += 1
            db.commit()
            continue

        # Se for convênio IPASGO (ID 6)
        if job.id_convenio == 6:
            rotina = str(job.rotina).lower()
            res_data = job.result_data
            
            if (rotina in ["6", "op6", "op6_check_baixados"]) and res_data:
                p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                numero_lote = p_params.get("numero_lote", p_params.get("loteId"))
                codigo_prestador = p_params.get("codigoPrestador", "")
                id_lote_interno = p_params.get("id_lote_interno")
                
                results_list = []
                if isinstance(res_data, list):
                    results_list = res_data
                elif isinstance(res_data, dict):
                    results_list = res_data.get("data", [])
                    if not isinstance(results_list, list):
                        if isinstance(res_data, dict) and "detalheId" in res_data:
                            results_list = [res_data]
                        else:
                            results_list = []
                
                from models import LoteConvenio, FaturamentoLote, LoteAgendamentoItem, LoteAgendamento
                lote_interno = None
                if id_lote_interno:
                    lote_interno = db.query(LoteConvenio).filter_by(id_lote=id_lote_interno).first()
                if not lote_interno and numero_lote:
                    lote_interno = db.query(LoteConvenio).filter(
                        LoteConvenio.numero_lote == int(numero_lote),
                        LoteConvenio.id_convenio == 6
                    ).first()
                
                if not lote_interno and numero_lote:
                    datas = []
                    for item in results_list:
                        dt_val = item.get('dataRealizacao')
                        if dt_val:
                            try:
                                if isinstance(dt_val, str):
                                    dt = datetime.fromisoformat(dt_val.split('T')[0])
                                    datas.append(dt.date())
                            except Exception:
                                pass
                    data_inicio_lote = min(datas) if datas else None
                    data_fim_lote = max(datas) if datas else None
                    
                    lote_interno = LoteConvenio(
                        id_convenio=6,
                        numero_lote=int(numero_lote),
                        cod_prestador=codigo_prestador,
                        status="Aberto",
                        user_id=job.user_id,
                        data_inicio=data_inicio_lote,
                        data_fim=data_fim_lote
                    )
                    db.add(lote_interno)
                    db.flush()
                
                lote_interno_id = lote_interno.id_lote if lote_interno else None
                
                if results_list and lote_interno_id:
                    detalhe_ids = [item['detalheId'] for item in results_list if 'detalheId' in item]
                    existing_items = {}
                    chunk_size = 900
                    for i in range(0, len(detalhe_ids), chunk_size):
                        chunk = detalhe_ids[i:i+chunk_size]
                        db_items = db.query(FaturamentoLote).filter(FaturamentoLote.detalheId.in_(chunk)).all()
                        for db_item in db_items:
                            existing_items[db_item.detalheId] = db_item
                    
                    now_utc = datetime.now(timezone.utc)
                    lotes_ag_para_reconciliar = set()
                    
                    for item in results_list:
                        det_id = item['detalheId']
                        existing = existing_items.get(det_id)
                        new_data_realizacao = item.get('dataRealizacao')
                        if new_data_realizacao and isinstance(new_data_realizacao, str):
                            try:
                                new_data_realizacao = datetime.fromisoformat(new_data_realizacao.split('T')[0]).date()
                            except:
                                pass
                        
                        new_status = item.get('StatusConferencia', 0)
                        
                        if existing:
                            data_mudou = str(existing.dataRealizacao) != str(new_data_realizacao)
                            status_era_conferido = existing.StatusConferencia == 67
                            status_mudou = existing.StatusConferencia != new_status
                            
                            deve_desvincular = (
                                existing.agendamento_id is not None and
                                (data_mudou or (status_era_conferido and status_mudou))
                            )
                            
                            if deve_desvincular:
                                lai = db.query(LoteAgendamentoItem).filter(
                                    LoteAgendamentoItem.id_faturamento_lote == existing.id
                                ).first()
                                if lai:
                                    lotes_ag_para_reconciliar.add(lai.id_lote_ag)
                                    lai.status_conciliacao = "Não Conciliado"
                                    lai.id_faturamento_lote = None
                                
                                existing.agendamento_id = None
                                existing.StatusConciliacao = "pendente"
                            
                            existing.dataRealizacao = new_data_realizacao
                            existing.Guia = str(item.get('Guia', ''))
                            existing.StatusConferencia = new_status
                            existing.ValorProcedimento = item.get('ValorProcedimento', 0.0)
                            existing.CodigoBeneficiario = item.get('CodigoBeneficiario', '')
                            existing.cod_procedimento_fat = item.get('cod_procedimento_fat', '')
                            existing.updated_at = now_utc
                            existing.id_lote = lote_interno_id
                            existing.user_id = job.user_id
                        else:
                            novo = FaturamentoLote(
                                detalheId=det_id,
                                CodigoBeneficiario=item.get('CodigoBeneficiario', ''),
                                dataRealizacao=new_data_realizacao,
                                Guia=str(item.get('Guia', '')),
                                StatusConferencia=new_status,
                                ValorProcedimento=item.get('ValorProcedimento', 0.0),
                                cod_procedimento_fat=item.get('cod_procedimento_fat', ''),
                                id_lote=lote_interno_id,
                                StatusConciliacao="pendente",
                                updated_at=now_utc,
                                user_id=job.user_id
                            )
                            db.add(novo)
                    
                    db.commit()
                    
                    if lotes_ag_para_reconciliar:
                        try:
                            from routes.conciliacao import process_conciliacao_bg
                            for id_lote_ag in lotes_ag_para_reconciliar:
                                lote_ag = db.query(LoteAgendamento).filter_by(id_lote_ag=id_lote_ag).first()
                                if lote_ag and lote_ag.id_lote_convenio:
                                    process_conciliacao_bg(lote_ag.id_lote_convenio, id_lote_ag, job.user_id)
                        except Exception as e:
                            print(f"Error triggering background reconciliation: {e}")
                
                job.result_consumed = True
                synced_counts["jobs_processed"] += 1
                db.commit()
                continue
                
            elif (rotina in ["7", "op7", "op7_fat_facplan"]) and isinstance(res_data, dict):
                itens_sucesso = res_data.get("itens_sucesso", [])
                p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                status_env = p_params.get("status")
                
                status_map = {}
                if not status_env:
                    itens_param = p_params.get("itens", [])
                    status_map = {str(it.get("detalheId")): it.get("status") for it in itens_param}
                
                from models import FaturamentoLote
                if itens_sucesso:
                    fats = db.query(FaturamentoLote).filter(
                        FaturamentoLote.detalheId.in_(itens_sucesso)
                    ).all()
                    for f in fats:
                        f.StatusConferencia = status_map.get(str(f.detalheId), status_env or 67)
                        f.updated_at = datetime.now(timezone.utc)
                    db.commit()
                
                job.result_consumed = True
                synced_counts["jobs_processed"] += 1
                db.commit()
                continue
                
            elif (rotina in ["13", "op13", "op13_criar_lote"]) and isinstance(res_data, dict):
                id_lote_interno = res_data.get("id_lote_interno")
                cod_prestador = res_data.get("cod_prestador")
                data_fim = res_data.get("data_fim")
                
                from models import LoteConvenio, Job as JobModel
                if id_lote_interno:
                    lote_obj = db.query(LoteConvenio).filter_by(id_lote=id_lote_interno).first()
                    if lote_obj:
                        lote_obj.status = "Criando"
                        db.commit()
                
                if data_fim:
                    parts = data_fim.split('/')
                    data_fim_iso = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else data_fim
                    poll_params = {
                        "cod_prestador": cod_prestador,
                        "data_fim": data_fim,
                        "data_fim_iso": data_fim_iso,
                        "id_lote_interno": id_lote_interno,
                        "poll_attempt": 0
                    }
                    new_job = JobModel(
                        id_convenio=job.id_convenio,
                        rotina="13_poll",
                        params=json.dumps(poll_params),
                        status="pending",
                        priority=10,
                        user_id=job.user_id
                    )
                    db.add(new_job)
                    db.commit()
                
                job.result_consumed = True
                synced_counts["jobs_processed"] += 1
                db.commit()
                continue
                
            elif (rotina in ["13_poll", "op13_poll"]) and isinstance(res_data, dict):
                status_lote = res_data.get("status")
                id_lote_interno = res_data.get("id_lote_interno")
                lote_id_api = res_data.get("lote_id_api")
                cod_prestador = res_data.get("cod_prestador")
                poll_attempt = res_data.get("poll_attempt", 0)
                
                from models import LoteConvenio, Job as JobModel
                if status_lote == "ready" and lote_id_api:
                    if id_lote_interno:
                        lote_obj = db.query(LoteConvenio).filter_by(id_lote=id_lote_interno).first()
                        if lote_obj:
                            lote_obj.numero_lote = lote_id_api
                            lote_obj.status = "Aberto"
                            db.commit()
                    
                    op6_params = {
                        "codigoPrestador": cod_prestador,
                        "numero_lote": lote_id_api,
                        "id_lote_interno": id_lote_interno
                    }
                    new_job = JobModel(
                        id_convenio=job.id_convenio,
                        rotina="6",
                        params=json.dumps(op6_params),
                        status="pending",
                        priority=10,
                        user_id=job.user_id
                    )
                    db.add(new_job)
                    db.commit()
                elif status_lote == "processing":
                    p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                    poll_params = {
                        "cod_prestador": cod_prestador,
                        "data_fim": p_params.get("data_fim"),
                        "data_fim_iso": p_params.get("data_fim_iso"),
                        "id_lote_interno": id_lote_interno,
                        "poll_attempt": poll_attempt + 1
                    }
                    new_job = JobModel(
                        id_convenio=job.id_convenio,
                        rotina="13_poll",
                        params=json.dumps(poll_params),
                        status="pending",
                        priority=15,
                        user_id=job.user_id
                    )
                    db.add(new_job)
                    db.commit()
                
                job.result_consumed = True
                synced_counts["jobs_processed"] += 1
                db.commit()
                continue
                
            elif rotina in ["14", "op14", "op14_cancelar_lote"]:
                p_params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
                id_lote_interno = p_params.get("id_lote_interno")
                numero_lote = p_params.get("numero_lote")
                
                from models import LoteConvenio, FaturamentoLote
                lote_obj = None
                if id_lote_interno:
                    lote_obj = db.query(LoteConvenio).filter_by(id_lote=id_lote_interno).first()
                if not lote_obj and numero_lote:
                    lote_obj = db.query(LoteConvenio).filter_by(numero_lote=numero_lote).first()
                
                if lote_obj:
                    lote_obj.status = "Cancelado"
                    items = db.query(FaturamentoLote).filter_by(id_lote=lote_obj.id_lote).all()
                    for item in items:
                        item.StatusConciliacao = "bloqueado"
                    db.commit()
                
                job.result_consumed = True
                synced_counts["jobs_processed"] += 1
                db.commit()
                continue

        # Se for convênio Unimeds (ID 2 ou 3)
        if job.id_convenio in [2, 3]:
            rotina = str(job.rotina).lower()
            res_data = job.result_data
            
            if rotina in ["3", "op3_execucao"] and res_data:
                results_list = []
                if isinstance(res_data, list):
                    results_list = res_data
                elif isinstance(res_data, dict):
                    results_list = res_data.get("data", [])
                    if not isinstance(results_list, list):
                        results_list = [results_list]
                        
                from models import Agendamento
                for item in results_list:
                    ag_id = item.get("agendamento_id")
                    executado = item.get("executado", False)
                    if ag_id and executado:
                        agenda = db.query(Agendamento).filter(Agendamento.id_agendamento == int(ag_id)).first()
                        if agenda:
                            agenda.execucao_status = "sucesso"
                db.commit()
                
                job.result_consumed = True
                synced_counts["jobs_processed"] += 1
                db.commit()
                continue

            # Unimed Goiania (id_convenio=3), rotina op1_consulta:
            # o worker devolve result_data.valida_prestador com {tipo_json, guias}.
            # IMPORTANTE: NAO fazemos `continue` aqui - deixamos o fluxo cair no
            # fallback generico abaixo, que executa bulk_upsert_guias_from_json e
            # insere/atualiza as guias em public.base_guias. Apos o upsert, aplicamos
            # o valida_prestador (no finally do loop). Para sinalizar que este job
            # tem valida_prestador para aplicar, marcamos um flag no synced_counts.
            if job.id_convenio == 3 and rotina in ["1", "op1_consulta", "consulta_guias"] and res_data:
                valida = res_data.get("valida_prestador") if isinstance(res_data, dict) else None
                if isinstance(valida, dict) and valida.get("guias"):
                    # Flag: apos o bulk_upsert, aplicar valida_prestador para as guias deste job.
                    synced_counts.setdefault("_pending_valida_prestador", [])
                    synced_counts["_pending_valida_prestador"].append({
                        "job_id": job.id,
                        "valida": valida,
                    })

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

            # Apos o bulk_upsert, as guias deste job ja existem em base_guias.
            # Se houver valida_prestador pendente para este job, aplica-lo agora.
            pending = synced_counts.get("_pending_valida_prestador") or []
            pending_for_job = [p for p in pending if p.get("job_id") == job.id]
            if pending_for_job:
                persisted = 0
                for entry in pending_for_job:
                    valida = entry.get("valida") or {}
                    for guia_num, attr in (valida.get("guias") or {}).items():
                        codigo_proc = (attr or {}).get("codigo_procedimento") or (attr or {}).get("codigo_terapia")
                        persisted += _persist_valida_prestador(db, guia_num, codigo_proc, attr or {})
                synced_counts.setdefault("valida_prestador_persisted", 0)
                synced_counts["valida_prestador_persisted"] += persisted
                # Remove da lista de pendentes
                synced_counts["_pending_valida_prestador"] = [
                    p for p in pending if p.get("job_id") != job.id
                ]

        job.result_consumed = True
        synced_counts["jobs_processed"] += 1
        
    if synced_counts["jobs_processed"] > 0:
        db.commit()
        try:
            from cache import cache
            for j in jobs:
                if j.user_id:
                    cache.invalidate_tenant(j.user_id)
        except Exception as e:
            print(f"Error invalidating cache after sync_completed_worker_jobs: {e}")
        
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


def _normalize_and_resolve_convenio_id(db: Session, conv_text_or_id: str | int | None = None, pagamento_id: int | None = None, is_carteirinha: bool = False) -> tuple[int, str]:
    """
    Mapeia texto do convênio ou pagamento_id para (id_convenio, nome_convenio).
    Regras estritas:
    1. IDs Fixos (3, 6, 8, 9, 21, 31): vinculação direta por ID no banco.
    2. Demais convênios: match EXATO e ÚNICO pelo nome do convênio. Se não existir, cadastra novo convênio com nome real.
    3. Carteirinhas (is_carteirinha=True): de-para de texto específico:
       - IPASGO - TEA -> IPASGO (id=6)
       - Unimed Goiânia Guia -> Unimed Goiânia (id=3)
       - Sulamérica -> SULAMERICA (id=8)
    """
    from models import Convenio
    import unicodedata

    def strip_accents(text):
        if not text: return ""
        return "".join(c for c in unicodedata.normalize('NFD', str(text)) if unicodedata.category(c) != 'Mn').lower().strip()

    KNOWN_IDS = {
        3: "UNIMED GOIANIA",
        6: "IPASGO",
        8: "SULAMERICA",
        9: "AMIL",
        21: "UNIMED INTERCAMBIO",
        31: "IPASGO - GERAL"
    }

    # 1. Tentar por pagamento_id se for um dos IDs conhecidos
    if pagamento_id is not None:
        try:
            pid = int(pagamento_id)
            if pid in KNOWN_IDS:
                conv = db.query(Convenio).filter(Convenio.id_convenio == pid).first()
                nome = conv.nome if conv else KNOWN_IDS[pid]
                return (pid, nome)
        except (ValueError, TypeError):
            pass

    # 2. Tentar por conv_text_or_id se for numérico e for um dos IDs conhecidos
    if conv_text_or_id is not None:
        try:
            cid = int(conv_text_or_id)
            if cid in KNOWN_IDS:
                conv = db.query(Convenio).filter(Convenio.id_convenio == cid).first()
                nome = conv.nome if conv else KNOWN_IDS[cid]
                return (cid, nome)
        except (ValueError, TypeError):
            pass

        raw_str = str(conv_text_or_id).strip()
        clean_str = strip_accents(raw_str)

        if not clean_str:
            return (None, None)

        # 3. Parse específico de nomes exclusivamente para carteirinhas
        if is_carteirinha:
            if "ipasgo" in clean_str and "geral" not in clean_str and "eventual" not in clean_str:
                return (6, "IPASGO")
            if "unimed goiania" in clean_str or "unimed goiânia" in clean_str or clean_str == "unimed goiania guia":
                return (3, "UNIMED GOIANIA")
            if "sulamerica" in clean_str or "sul america" in clean_str or "sulamérica" in clean_str:
                return (8, "SULAMERICA")

        # 4. Match EXATO e ÚNICO por igualdade de nome para todos os outros convênios
        all_convs = db.query(Convenio).all()
        for c in all_convs:
            if strip_accents(c.nome) == clean_str:
                return (c.id_convenio, c.nome)

        # Se não encontrou por nome exato, insere novo convênio com o nome real
        conv_db = Convenio(nome=raw_str)
        db.add(conv_db)
        db.flush()
        return (conv_db.id_convenio, conv_db.nome)

    return (None, None)


def _sync_aba_clmf_op1(db: Session, job, agendamentos_data: list, synced_counts: dict):
    """
    Processa resultados da OP1 (Importar Agendamentos) do ABA_clmf.
    Garante que 100% dos agendamentos do JSON sejam salvos imediatamente na tabela agendamentos em alta velocidade.
    """
    from models import Agendamento, Carteirinha, Convenio, Job as JobModel

    if not agendamentos_data:
        return

    # Pre-fetch convênios and carteirinhas in bulk for extreme performance (no N+1 queries)
    all_convs = db.query(Convenio).all()
    convs_by_id = {c.id_convenio: c for c in all_convs}
    
    import unicodedata
    def _strip_accents(text):
        if not text: return ""
        return "".join(c for c in unicodedata.normalize('NFD', str(text)) if unicodedata.category(c) != 'Mn').lower().strip()
    
    convs_by_clean_name = {_strip_accents(c.nome): c for c in all_convs}

    def resolve_conv_fast(conv_text_or_id, pagamento_id):
        KNOWN_IDS = {3: "UNIMED GOIANIA", 6: "IPASGO", 8: "SULAMERICA", 9: "AMIL", 21: "UNIMED INTERCAMBIO", 31: "IPASGO - GERAL"}
        # 1. Busca por pagamento_id SOMENTE se for um dos IDs conhecidos
        if pagamento_id is not None:
            try:
                pid = int(pagamento_id)
                if pid in KNOWN_IDS:
                    c = convs_by_id.get(pid)
                    return (pid, c.nome if c else KNOWN_IDS[pid])
            except (ValueError, TypeError): pass

        # 2. Busca por id numérico SOMENTE se for um dos IDs conhecidos
        if conv_text_or_id is not None:
            try:
                cid = int(conv_text_or_id)
                if cid in KNOWN_IDS:
                    c = convs_by_id.get(cid)
                    return (cid, c.nome if c else KNOWN_IDS[cid])
            except (ValueError, TypeError): pass

            raw_str = str(conv_text_or_id).strip()
            clean_str = _strip_accents(raw_str)
            if clean_str:
                # 3. Match EXATO e ÚNICO pelo nome do convênio (ex: Social/Gratuidade)
                if clean_str in convs_by_clean_name:
                    c = convs_by_clean_name[clean_str]
                    return (c.id_convenio, c.nome)
                # 4. Se o nome não existir no banco, cadastra o novo convênio com o nome real
                new_c = Convenio(nome=raw_str)
                db.add(new_c)
                db.flush()
                convs_by_id[new_c.id_convenio] = new_c
                convs_by_clean_name[clean_str] = new_c
                return (new_c.id_convenio, new_c.nome)

        return (None, None)

    # Bulk pre-fetch all user carteirinhas
    all_carts = db.query(Carteirinha).filter(Carteirinha.user_id == job.user_id).all()
    carts_by_pac_conv = {(str(c.id_paciente).strip(), c.id_convenio): c for c in all_carts if c.id_paciente}

    # Pre-fetch pending OP2 jobs to avoid querying inside loop
    existing_op2_jobs = db.query(JobModel).filter(
        JobModel.id_convenio == 101,
        JobModel.rotina.in_(["op2_consultar_carteirinha", "op2"]),
        JobModel.status.in_(["pending", "processing"]),
        JobModel.user_id == job.user_id
    ).all()
    
    op2_patients_map = {}
    for p_job in existing_op2_jobs:
        try:
            p_params = json.loads(p_job.params) if isinstance(p_job.params, str) else (p_job.params or {})
            pid = str(p_params.get("id_paciente", "")).strip()
            if pid:
                op2_patients_map[pid] = (p_job, p_params)
        except Exception: pass

    op2_new_payloads = {}

    ag_dicts_to_upsert = []
    from datetime import datetime

    for item in agendamentos_data:
        if not isinstance(item, dict):
            continue

        id_ag = item.get("id_agendamento")
        id_paciente = str(item.get("id_paciente", "")).strip()
        id_unidade = item.get("id_unidade", 0)

        if not id_ag or not id_paciente:
            continue

        if id_unidade not in (1, 3, 5):
            synced_counts["skipped"] += 1
            continue

        pagamento_id = _parse_int(item.get("schedule_pagamento_id"), default=0)
        id_convenio, nome_conv = resolve_conv_fast(item.get("convenio_nome"), pagamento_id)

        cart = carts_by_pac_conv.get((id_paciente, id_convenio))
        id_carteirinha = cart.id if cart else None
        carteirinha_num = cart.carteirinha if cart else ""

        if not cart:
            if id_paciente in op2_patients_map:
                p_job, p_params = op2_patients_map[id_paciente]
                ag_list = p_params.get("agendamentos_pendentes") or []
                ag_list.append(item)
                p_params["agendamentos_pendentes"] = ag_list
                p_job.params = json.dumps(p_params)
            elif id_paciente not in op2_new_payloads:
                op2_new_payloads[id_paciente] = [item]
            else:
                op2_new_payloads[id_paciente].append(item)

        data_val = item.get("data")
        if isinstance(data_val, str) and data_val:
            try: data_val = datetime.strptime(data_val[:10], "%Y-%m-%d").date()
            except ValueError: data_val = None

        hora_val = item.get("hora_inicio")
        if isinstance(hora_val, str) and hora_val:
            try: hora_val = datetime.strptime(hora_val[:8], "%H:%M:%S").time()
            except ValueError:
                try: hora_val = datetime.strptime(hora_val[:5], "%H:%M").time()
                except ValueError: hora_val = None

        ag_dicts_to_upsert.append({
            "id_agendamento": int(id_ag),
            "id_paciente": str(id_paciente),
            "id_unidade": int(id_unidade),
            "id_carteirinha": id_carteirinha,
            "carteirinha": carteirinha_num or "",
            "Nome_Paciente": item.get("Nome_Paciente"),
            "id_convenio": int(id_convenio) if id_convenio is not None else None,
            "nome_convenio": nome_conv,
            "data": data_val,
            "hora_inicio": hora_val,
            "sala": item.get("sala"),
            "Id_profissional": str(item.get("Id_profissional")),
            "Nome_profissional": item.get("Nome_profissional"),
            "Tipo_atendimento": item.get("Tipo_atendimento"),
            "cod_procedimento_aut": item.get("cod_procedimento_aut"),
            "cod_procedimento_fat": item.get("cod_procedimento_fat"),
            "valor_procedimento": 0.0,
            "Status": item.get("Status", "A Confirmar"),
            "user_id": job.user_id
        })

    if ag_dicts_to_upsert:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy import func

        # Deduplicate ag_dicts_to_upsert by id_agendamento in python first
        unique_ag_map = {d["id_agendamento"]: d for d in ag_dicts_to_upsert}
        ag_list_unique = list(unique_ag_map.values())

        stmt = pg_insert(Agendamento).values(ag_list_unique)
        update_set = {
            "id_paciente": stmt.excluded.id_paciente,
            "id_unidade": stmt.excluded.id_unidade,
            "Nome_Paciente": stmt.excluded.Nome_Paciente,
            "id_convenio": stmt.excluded.id_convenio,
            "nome_convenio": stmt.excluded.nome_convenio,
            "data": stmt.excluded.data,
            "hora_inicio": stmt.excluded.hora_inicio,
            "sala": stmt.excluded.sala,
            "Id_profissional": stmt.excluded.Id_profissional,
            "Nome_profissional": stmt.excluded.Nome_profissional,
            "Tipo_atendimento": stmt.excluded.Tipo_atendimento,
            "cod_procedimento_aut": stmt.excluded.cod_procedimento_aut,
            "cod_procedimento_fat": stmt.excluded.cod_procedimento_fat,
            "Status": stmt.excluded.Status,
            "user_id": stmt.excluded.user_id,
            "data_update": func.now()
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=['id_agendamento'],
            set_=update_set
        )
        db.execute(stmt)
        synced_counts["updated"] += len(ag_list_unique)

    # Queue new OP2 jobs for missing carteirinhas in bulk
    for pid, ag_items in op2_new_payloads.items():
        op2_params = json.dumps({
            "id_paciente": pid,
            "agendamentos_pendentes": ag_items
        })
        new_op2_job = JobModel(
            status="pending",
            id_convenio=101,
            user_id=job.user_id,
            rotina="op2_consultar_carteirinha",
            params=op2_params,
            priority=0
        )
        db.add(new_op2_job)
    # Processa atendimentos excluídos retornados pelo portal no job de importação
    excluidos_ids = []
    if isinstance(job.result_data, dict):
        excluidos_ids = job.result_data.get("atendimentos_excluidos") or []
        if not excluidos_ids and isinstance(job.result_data.get("data"), dict):
            excluidos_ids = job.result_data["data"].get("atendimentos_excluidos") or []
    elif isinstance(agendamentos_data, dict):
        excluidos_ids = agendamentos_data.get("atendimentos_excluidos") or []

    if excluidos_ids:
        excl_int_ids = []
        for x in excluidos_ids:
            try:
                excl_int_ids.append(int(x))
            except (ValueError, TypeError): pass

        if excl_int_ids:
            excl_ags = db.query(Agendamento).filter(
                Agendamento.id_agendamento.in_(excl_int_ids),
                Agendamento.user_id == job.user_id
            ).all()

            for ag in excl_ags:
                ag.Status = "Excluído"
                _unlink_guia_if_eligible(db, ag)

    db.commit()


def _sync_aba_clmf_op2(db: Session, job, results_list: list, synced_counts: dict):
    """
    Processa resultados da OP2 (Consultar Carteirinha) do ABA_clmf.
    Mapeia e cria carteirinhas vinculadas ao convenio de saude REAL (nunca 101) e atualiza agendamentos.
    """
    from models import Carteirinha, Convenio, Agendamento

    for res in results_list:
        if not isinstance(res, dict):
            continue

        id_paciente = str(res.get("id_paciente", "")).strip()
        carteirinhas_found = res.get("carteirinhas") or []
        agendamentos_pendentes = res.get("agendamentos_pendentes") or []

        if not id_paciente:
            continue

        # 1. Processar cada carteirinha encontrada no portal HTML
        for cart_item in carteirinhas_found:
            cart_num = str(cart_item.get("carteirinha", "")).strip()
            conv_texto = str(cart_item.get("convenio_texto", "")).strip()
            status_cart = str(cart_item.get("status", "Ativa")).strip()

            if not cart_num or not conv_texto:
                continue

            id_convenio, nome_conv = _normalize_and_resolve_convenio_id(db, conv_text_or_id=conv_texto, pagamento_id=None, is_carteirinha=True)

            # Garantir que nunca usamos id_convenio = 101 para carteirinhas
            if id_convenio == 101:
                id_convenio = 6
                nome_conv = "IPASGO"

            existing_cart = db.query(Carteirinha).filter(
                Carteirinha.carteirinha == cart_num,
                Carteirinha.user_id == job.user_id
            ).first()

            if not existing_cart:
                existing_cart = db.query(Carteirinha).filter(
                    Carteirinha.id_paciente == id_paciente,
                    Carteirinha.id_convenio == id_convenio,
                    Carteirinha.user_id == job.user_id
                ).first()

            patient_name = agendamentos_pendentes[0].get("Nome_Paciente") if agendamentos_pendentes else f"Paciente {id_paciente}"

            if not existing_cart:
                existing_cart = Carteirinha(
                    carteirinha=cart_num,
                    paciente=patient_name,
                    id_paciente=id_paciente,
                    id_convenio=id_convenio,
                    user_id=job.user_id,
                    status=status_cart.lower() if status_cart else "ativo"
                )
                try:
                    with db.begin_nested():
                        db.add(existing_cart)
                        db.flush()
                except Exception:
                    existing_cart = db.query(Carteirinha).filter(
                        Carteirinha.user_id == job.user_id,
                        (Carteirinha.id_paciente == id_paciente) | (Carteirinha.carteirinha == cart_num)
                    ).first()
            else:
                existing_cart.id_paciente = id_paciente
                existing_cart.id_convenio = id_convenio
                if cart_num:
                    existing_cart.carteirinha = cart_num

        # 2. Garantir que agendamentos pendentes recebam os updates necessários
        for ag in agendamentos_pendentes:
            if not isinstance(ag, dict):
                continue
            ag_conv_nome = ag.get("convenio_nome") or ag.get("nome_convenio")
            ag_pag_id = _parse_int(ag.get("schedule_pagamento_id"), default=0)
            ag_conv_id, _ = _normalize_and_resolve_convenio_id(db, ag_conv_nome, pagamento_id=ag_pag_id)

            matching_cart = db.query(Carteirinha).filter(
                Carteirinha.id_paciente == id_paciente,
                Carteirinha.id_convenio == ag_conv_id,
                Carteirinha.user_id == job.user_id
            ).first()

            cart_id = matching_cart.id if matching_cart else None
            cart_num = matching_cart.carteirinha if matching_cart else ""

            _upsert_agendamento(db, ag, cart_id, cart_num, ag_conv_id, job.user_id, synced_counts)


def _upsert_agendamento(db: Session, item: dict, id_carteirinha: int | None, carteirinha_num: str | None, id_convenio: int | str, user_id: int, synced_counts: dict, nome_conv_override: str | None = None):
    """
    Auxiliar para realizar UPSERT atomico na tabela agendamentos via pg_insert ON CONFLICT DO UPDATE.
    """
    from models import Agendamento, Convenio
    from datetime import datetime, date, time
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy import func

    id_ag = int(item["id_agendamento"])
    if nome_conv_override:
        nome_conv = nome_conv_override
        id_convenio = int(id_convenio)
    elif isinstance(id_convenio, int) or (isinstance(id_convenio, str) and id_convenio.isdigit()):
        conv = db.query(Convenio).filter(Convenio.id_convenio == int(id_convenio)).first()
        nome_conv = conv.nome if conv else ""
        id_convenio = int(id_convenio)
    else:
        id_convenio, nome_conv = _normalize_and_resolve_convenio_id(db, conv_text_or_id=id_convenio)

    data_val = item.get("data")
    if isinstance(data_val, str) and data_val:
        try:
            data_val = datetime.strptime(data_val[:10], "%Y-%m-%d").date()
        except ValueError:
            data_val = None

    hora_val = item.get("hora_inicio")
    if isinstance(hora_val, str) and hora_val:
        try:
            hora_val = datetime.strptime(hora_val[:8], "%H:%M:%S").time()
        except ValueError:
            try:
                hora_val = datetime.strptime(hora_val[:5], "%H:%M").time()
            except ValueError:
                hora_val = None

    ag_dict = {
        "id_agendamento": id_ag,
        "id_paciente": str(item.get("id_paciente")),
        "id_unidade": int(item.get("id_unidade", 0)),
        "id_carteirinha": id_carteirinha,
        "carteirinha": carteirinha_num or "",
        "Nome_Paciente": item.get("Nome_Paciente"),
        "id_convenio": id_convenio,
        "nome_convenio": nome_conv,
        "data": data_val,
        "hora_inicio": hora_val,
        "sala": item.get("sala"),
        "Id_profissional": str(item.get("Id_profissional")),
        "Nome_profissional": item.get("Nome_profissional"),
        "Tipo_atendimento": item.get("Tipo_atendimento"),
        "cod_procedimento_aut": item.get("cod_procedimento_aut"),
        "cod_procedimento_fat": item.get("cod_procedimento_fat"),
        "valor_procedimento": 0.0,
        "Status": item.get("Status", "A Confirmar"),
        "user_id": user_id
    }

    stmt = pg_insert(Agendamento).values(ag_dict)

    update_set = {
        "id_paciente": stmt.excluded.id_paciente,
        "id_unidade": stmt.excluded.id_unidade,
        "Nome_Paciente": stmt.excluded.Nome_Paciente,
        "id_convenio": stmt.excluded.id_convenio,
        "nome_convenio": stmt.excluded.nome_convenio,
        "data": stmt.excluded.data,
        "hora_inicio": stmt.excluded.hora_inicio,
        "sala": stmt.excluded.sala,
        "Id_profissional": stmt.excluded.Id_profissional,
        "Nome_profissional": stmt.excluded.Nome_profissional,
        "Tipo_atendimento": stmt.excluded.Tipo_atendimento,
        "cod_procedimento_aut": stmt.excluded.cod_procedimento_aut,
        "cod_procedimento_fat": stmt.excluded.cod_procedimento_fat,
        "Status": stmt.excluded.Status,
        "user_id": stmt.excluded.user_id,
        "data_update": func.now()
    }

    if id_carteirinha is not None:
        update_set["id_carteirinha"] = stmt.excluded.id_carteirinha
    if carteirinha_num:
        update_set["carteirinha"] = stmt.excluded.carteirinha

    stmt = stmt.on_conflict_do_update(
        index_elements=['id_agendamento'],
        set_=update_set
    )
    db.execute(stmt)
    synced_counts["updated"] += 1



