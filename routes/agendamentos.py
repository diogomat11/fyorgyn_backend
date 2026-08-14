from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, time

from models import (
    Agendamento,
    Carteirinha,
    Convenio,
    CorpoClinico,
    Procedimento,
    ProcedimentoFaturamento,
    BaseGuia
)
from dependencies import get_current_user, get_current_worker_key, get_effective_user_id
from sqlalchemy import func, String, cast, or_

router = APIRouter(
    prefix="/agendamentos",
    tags=["Agendamentos"]
)

@router.get("/procedimentos")
def list_procedimentos_agendamentos(id_convenio: Optional[int] = None, db: Session = Depends(get_db)):
    """Retorna procedimentos ativos do convênio especificado ou todos se id_convenio não fornecido."""
    query = db.query(Procedimento).filter(Procedimento.status == "ativo")
    if id_convenio:
        query = query.filter(Procedimento.id_convenio == id_convenio)
    procs = query.order_by(Procedimento.nome).all()
    return [
        {
            "id": p.id_procedimento,
            "codigo": p.codigo_procedimento,
            "nome": p.nome,
            "faturamento": p.faturamento
        }
        for p in procs
    ]

class CreateAgendamentoRequest(BaseModel):
    carteirinha: str
    id_convenio: int
    Id_profissional: str
    cod_procedimento_aut: str
    data: date
    hora_inicio: time
    sala: Optional[str] = None
    Tipo_atendimento: str
    Status: str = "A Confirmar"

from dependencies import get_current_user

@router.post("/")
def create_agendamento(req: CreateAgendamentoRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    # 1. Load dependencies based on input
    
    query = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == req.carteirinha,
        Carteirinha.id_convenio == req.id_convenio
    )
    if not current_user.is_admin:
        query = query.filter(Carteirinha.user_id == get_effective_user_id(current_user))
    cart = query.first()
    
    if not cart:
        # Tenta buscar ignorando o convenio, caso seja cart universal ou errada
        query2 = db.query(Carteirinha).filter(Carteirinha.carteirinha == req.carteirinha)
        if not current_user.is_admin:
            query2 = query2.filter(Carteirinha.user_id == get_effective_user_id(current_user))
        cart = query2.first()
        if not cart:
            raise HTTPException(status_code=404, detail="Carteirinha não encontrada.")

    # Se não for admin, validar posse da carteirinha
    if not current_user.is_admin and cart.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para esta carteirinha.")

    # Check Convenio
    conv = db.query(Convenio).filter(Convenio.id_convenio == req.id_convenio).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Convênio não encontrado.")

    # Check Profissional
    prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == req.Id_profissional).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Profissional não encontrado.")

    # Check Procedimento
    proc = db.query(Procedimento).filter(
        Procedimento.autorizacao == req.cod_procedimento_aut,
        Procedimento.id_convenio == req.id_convenio
    ).first()
    
    if not proc:
        raise HTTPException(status_code=404, detail="Procedimento (Autorização) não encontrado para este convênio.")

    # Check Faturamento / Valor
    proc_fat = db.query(ProcedimentoFaturamento).filter(
        ProcedimentoFaturamento.id_procedimento == proc.id_procedimento,
        ProcedimentoFaturamento.id_convenio == req.id_convenio
    ).first()
    
    valor = proc_fat.valor if proc_fat else 0.0

    # 2. Build Extrapolated Agendamento
    new_agendamento = Agendamento(
        id_paciente=cart.id_paciente,
        id_carteirinha=cart.id,
        carteirinha=cart.carteirinha,
        Nome_Paciente=cart.paciente,
        id_convenio=conv.id_convenio,
        nome_convenio=conv.nome,
        data=req.data,
        hora_inicio=req.hora_inicio,
        sala=req.sala,
        Id_profissional=prof.id_profissional,
        Nome_profissional=prof.nome,
        Tipo_atendimento=req.Tipo_atendimento,
        id_procedimento=proc.id_procedimento,
        cod_procedimento_fat=proc.faturamento,
        nome_procedimento=proc.nome,
        valor_procedimento=valor,
        cod_procedimento_aut=proc.autorizacao,
        Status=req.Status,
        user_id=get_effective_user_id(current_user)
    )

    try:
        db.add(new_agendamento)
        db.commit()
        db.refresh(new_agendamento)
        try:
            from cache import cache
            cache.invalidate_tenant(get_effective_user_id(current_user))
        except Exception:
            pass
        return {"status": "success", "agendamento": new_agendamento}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/vincular-guias")
def vincular_guias_manualmente(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """
    Desperta deliberadamente o Trigger Mestre das Guias.
    Somente guias ainda válidas (com saldo) serão "puxadas" acordando a tabela, 
    vasculhando qualquer Agendamento orfão pendente na agenda elegível para descontos.
    """
    try:
        query = db.query(BaseGuia).filter(
            BaseGuia.saldo > 0,
            or_(
                BaseGuia.status_guia.ilike('%autorizad%'),
                BaseGuia.status_guia.ilike('%liberad%')
            )
        )
        if not current_user.is_admin:
            query = query.filter(BaseGuia.user_id == get_effective_user_id(current_user))
            
        updated = query.update({
            BaseGuia.updated_at: func.now()
        }, synchronize_session=False)
        db.commit()
        try:
            from cache import cache
            cache.invalidate_tenant(get_effective_user_id(current_user))
        except Exception:
            pass
        return {"status": "success", "message": f"Varredura de Guias ativada. Lotes com saldo positivo reagiram ao pulso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao forçar vinculação de guias: {str(e)}")


class SincronizarAgendamentosRequest(BaseModel):
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    id_paciente: Optional[str] = "0"
    id_convenio: Optional[int] = 101


@router.post("/sincronizar")
def sincronizar_agendamentos_portal(
    req: SincronizarAgendamentosRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    from models import UserConvenio, Job
    import json

    target_id_convenio = req.id_convenio or 101
    
    # Verifica se o usuario tem credenciais cadastradas para o convenio ABA_clmf (ou id_convenio alvo)
    user_conv = db.query(UserConvenio).filter(
        UserConvenio.user_id == current_user.id,
        UserConvenio.id_convenio == target_id_convenio
    ).first()

    if not user_conv:
        raise HTTPException(
            status_code=400,
            detail=f"Usuário não possui credenciais vinculadas para o convênio ID {target_id_convenio} (ABA_clmf)."
        )

    params_dict = {
        "data_inicio": req.data_inicio.strftime("%Y-%m-%d") if req.data_inicio else None,
        "data_fim": req.data_fim.strftime("%Y-%m-%d") if req.data_fim else None,
        "id_paciente": req.id_paciente or "0"
    }

    new_job = Job(
        id_convenio=target_id_convenio,
        rotina="op1_importar_agendamentos",
        status="pending",
        params=json.dumps(params_dict),
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return {
        "status": "success",
        "message": f"Job #{new_job.id} de sincronização de agendamentos criado com sucesso!",
        "job_id": new_job.id
    }

@router.get("/")
def list_agendamentos(
    paciente: Optional[str] = None,
    id_convenio: Optional[int] = None,
    id_unidade: Optional[int] = None,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    status: Optional[str] = None,
    procedimento: Optional[str] = None,
    sem_carteirinha: Optional[bool] = False,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    target_user_id = get_effective_user_id(current_user)
    
    # 1. Lookup Redis Cache
    cache_params = {
        "paciente": paciente,
        "id_convenio": id_convenio,
        "id_unidade": id_unidade,
        "data_inicio": str(data_inicio) if data_inicio else None,
        "data_fim": str(data_fim) if data_fim else None,
        "status": status,
        "procedimento": procedimento,
        "sem_carteirinha": sem_carteirinha,
        "limit": limit,
        "skip": skip
    }
    from cache import cache
    cached_res = cache.get(target_user_id, "agendamentos", cache_params)
    if cached_res:
        return cached_res

    base_query = db.query(Agendamento)
    if not current_user.is_admin:
        base_query = base_query.filter(Agendamento.user_id == target_user_id)
    
    if paciente:
        base_query = base_query.filter(Agendamento.Nome_Paciente.ilike(f"%{paciente}%"))
    if id_convenio:
        base_query = base_query.filter(Agendamento.id_convenio == id_convenio)
    if id_unidade:
        base_query = base_query.filter(Agendamento.id_unidade == id_unidade)
    if data_inicio:
        base_query = base_query.filter(Agendamento.data >= data_inicio)
    if data_fim:
        base_query = base_query.filter(Agendamento.data <= data_fim)
    if procedimento:
        base_query = base_query.filter(
            (Agendamento.nome_procedimento.ilike(f"%{procedimento}%")) |
            (Agendamento.cod_procedimento_fat.ilike(f"%{procedimento}%")) |
            (Agendamento.cod_procedimento_aut.ilike(f"%{procedimento}%"))
        )
    if sem_carteirinha:
        base_query = base_query.filter(
            (Agendamento.carteirinha == None) | (func.trim(Agendamento.carteirinha) == '')
        )

    # Calculate KPIs from base_query BEFORE status filtering in ONE single aggregated query
    pendentes_condition = (Agendamento.Status == 'Pendente') | (
        (Agendamento.Status == 'Confirmado') & (
            (Agendamento.numero_guia == None) | (func.trim(Agendamento.numero_guia) == '')
        )
    )
    
    from sqlalchemy import case
    base_filters = base_query.whereclause.clauses if hasattr(base_query.whereclause, 'clauses') else [base_query.whereclause] if base_query.whereclause is not None else []
    
    kpi_aggregations = db.query(
        func.count(Agendamento.id_agendamento).label("total_base"),
        func.count(case((Agendamento.Status == 'Confirmado', 1))).label("confirmados"),
        func.count(case((Agendamento.Status == 'A Confirmar', 1))).label("a_confirmar"),
        func.count(case((Agendamento.Status == 'Falta', 1))).label("faltas"),
        func.count(case((Agendamento.Status.in_(['Faturado', 'Faturamento Solicitado']), 1))).label("faturados"),
        func.count(case((pendentes_condition, 1))).label("pendentes")
    ).filter(*base_filters).first()

    confirmados_count = kpi_aggregations.confirmados if kpi_aggregations else 0
    a_confirmar_count = kpi_aggregations.a_confirmar if kpi_aggregations else 0
    faltas_count = kpi_aggregations.faltas if kpi_aggregations else 0
    faturados_count = kpi_aggregations.faturados if kpi_aggregations else 0
    pendentes_count = kpi_aggregations.pendentes if kpi_aggregations else 0

    query = base_query
    if status:
        if status.lower() == 'pendentes':
            query = query.filter(pendentes_condition)
        elif status.lower() == 'faturado':
            query = query.filter(Agendamento.Status.in_(['Faturado', 'Faturamento Solicitado']))
        else:
            query = query.filter(Agendamento.Status == status)
        total = query.count()
    else:
        total = kpi_aggregations.total_base if kpi_aggregations else 0
    
    # Outer join to fetch the Saldo da Guia if numero_guia is populated
    from sqlalchemy.orm import aliased
    from models import BaseGuia, Unidade
    bg = aliased(BaseGuia)
    
    # Pre-fetch unidades for name mapping
    unidades_map = {u.id_unidade: u.nome for u in db.query(Unidade).all()}
    
    join_cond = (Agendamento.numero_guia == bg.guia)
    if not current_user.is_admin:
        join_cond = join_cond & (bg.user_id == target_user_id)

    query_filters = query.whereclause.clauses if hasattr(query.whereclause, 'clauses') else [query.whereclause] if query.whereclause is not None else []
    agendamentos = (
        db.query(Agendamento, bg.saldo.label("saldo_guia"), bg.timestamp_captura.label("timestamp_captura"))
        .outerjoin(bg, join_cond)
        .filter(*query_filters)
        .order_by(Agendamento.data.desc().nulls_last(), Agendamento.hora_inicio.desc().nulls_last())
        .limit(limit)
        .offset(skip)
        .all()
    )
    
    # Format the response map
    data = []
    for ag, saldo, ts_cap in agendamentos:
        dic = {c.name: getattr(ag, c.name) for c in ag.__table__.columns}
        dic["saldo_guia"] = saldo
        dic["timestamp_captura"] = ts_cap
        dic["nome_unidade"] = unidades_map.get(ag.id_unidade) if ag.id_unidade else None
        data.append(dic)
        
    res_payload = {
        "data": data, 
        "total": total, 
        "total_geral": total, 
        "skip": skip, 
        "limit": limit,
        "kpis": {
            "confirmados": confirmados_count,
            "a_confirmar": a_confirmar_count,
            "faltas": faltas_count,
            "faturados": faturados_count,
            "pendentes": pendentes_count,
            "sem_carteirinha": pendentes_count
        }
    }
    
    # Cache result for 20 seconds
    try:
        cache.set(target_user_id, "agendamentos", cache_params, res_payload, ttl=20)
    except Exception:
        pass
        
    return res_payload

@router.get("/procedimentos")
def list_procedimentos(id_convenio: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    from dependencies import get_allowed_convenio_ids
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")

    from models import Agendamento
    procs = db.query(Agendamento.nome_procedimento, Agendamento.cod_procedimento_aut)\
              .filter(Agendamento.id_convenio == id_convenio)
    
    if not current_user.is_admin:
        procs = procs.filter(Agendamento.user_id == get_effective_user_id(current_user))
        
    procs = procs.distinct().all()
    
    # Monta uma lista flat com nomes e códigos não nulos/vazios
    result_set = set()
    for p in procs:
        if p[0]: result_set.add(p[0])
        elif p[1]: result_set.add(p[1])
        
    return sorted(list(result_set))

class BatchStatusRequest(BaseModel):
    ids: List[int]
    status: str
    capturar_guias: bool = True

@router.put("/batch-status")
def batch_update_status(req: BatchStatusRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    from collections import Counter
    from models import BaseGuia

    # Se não for admin, verificar posse dos agendamentos
    if not current_user.is_admin:
        count_agendamentos = db.query(Agendamento).filter(
            Agendamento.id_agendamento.in_(req.ids),
            Agendamento.user_id == get_effective_user_id(current_user)
        ).count()
        if count_agendamentos != len(req.ids):
            raise HTTPException(status_code=403, detail="Um ou mais agendamentos não pertencem ao seu usuário.")

    agendamentos_to_update = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).all()
    
    # Se o status sendo setado e 'Falta', restituimos a guia
    if req.status == 'Falta':
        guia_counts = Counter([ag.numero_guia for ag in agendamentos_to_update if ag.numero_guia])
        
        # Devolve +1 no Saldo
        for guia_str, count in guia_counts.items():
            db.query(BaseGuia).filter(BaseGuia.guia == guia_str).update({BaseGuia.saldo: BaseGuia.saldo + count})
        
        # Desvincula a Guia Desses Agendamentos Falta
        db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).update({
            Agendamento.Status: req.status,
            Agendamento.numero_guia: None
        }, synchronize_session=False)
    else:
        db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).update({Agendamento.Status: req.status}, synchronize_session=False)
    
    jobs_created = 0
    if req.status == 'Confirmado' and req.capturar_guias:
        from models import Convenio, Job, Carteirinha
        import json
        for ag in agendamentos_to_update:
            if ag.id_convenio:
                conv = db.query(Convenio).filter(Convenio.id_convenio == ag.id_convenio).first()
                # Confirmar lote: Para Anápolis (2), cria par Captura + Execução dependente.
                # Para outros (como Goiânia 3), mantém apenas Captura isolada.
                should_create_capture = False
                if ag.id_convenio in (2, 3):
                    should_create_capture = True
                elif conv and (conv.biometria or conv.pei_automatico):
                    should_create_capture = True
                    
                # IPASGO does not need capture, just direct execution
                if ag.id_convenio == 6:
                    should_create_capture = False
                
                if should_create_capture:
                    cart = db.query(Carteirinha).filter(
                        Carteirinha.carteirinha == ag.carteirinha, 
                        Carteirinha.id_convenio == ag.id_convenio,
                        Carteirinha.user_id == ag.user_id
                    ).first()
                    if cart:
                        # 1. Busca ou Cria Captura
                        cap_job = None
                        if ag.numero_guia:
                            from sqlalchemy import cast, String
                            cap_job = db.query(Job).filter(
                                Job.id_convenio == ag.id_convenio,
                                Job.rotina.in_(["Captura", "op2_captura"]),
                                Job.status.in_(["pending", "processing", "success"]),
                                cast(Job.params, String).contains(ag.numero_guia)
                            ).first()
                        
                        if not cap_job:
                            cap_job = Job(
                                carteirinha_id=cart.id,
                                id_convenio=ag.id_convenio,
                                rotina="op2_captura",
                                status="pending",
                                params=json.dumps({"agendamento_id": ag.id_agendamento, "numero_guia": ag.numero_guia or ""}),
                                user_id=get_effective_user_id(current_user)
                            )
                            db.add(cap_job)
                            db.flush()
                            jobs_created += 1
 
                        # 2. Para Anápolis (2), cria também a Execução dependente se não existir
                        if ag.id_convenio == 2:
                            from sqlalchemy import cast, String
                            existing_exec = db.query(Job).filter(
                                Job.rotina.in_(["Execução", "op3_execucao"]),
                                Job.status.in_(["pending", "processing"]),
                                cast(Job.params, String).contains(str(ag.id_agendamento))
                            ).first()
 
                            if not existing_exec:
                                from models import CorpoClinico
                                from datetime import datetime
                                prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == ag.Id_profissional).first()
                                data_hora = ""
                                try:
                                    if ag.data and ag.hora_inicio:
                                        hora = ag.hora_inicio
                                        if isinstance(hora, str):
                                            try: hora = datetime.strptime(hora[:5], "%H:%M").time()
                                            except: pass
                                        data_hora = f"{ag.data.strftime('%d/%m/%Y')} {hora.strftime('%H:%M')}"
                                except: pass
 
                                exec_params = {
                                    "agendamento_id": ag.id_agendamento,
                                    "numero_guia": ag.numero_guia or "",
                                    "nome_profissional": prof.nome if prof else (ag.Nome_profissional or ""),
                                    "conselho": prof.conselho if prof else "",
                                    "data_hora": data_hora,
                                    "cod_procedimento_fat": ag.cod_procedimento_fat or ""
                                }
                                exec_job = Job(
                                    carteirinha_id=cart.id,
                                    id_convenio=cart.id_convenio,
                                    rotina="op3_execucao",
                                    status="pending",
                                    depending_id=cap_job.id,
                                    params=json.dumps(exec_params),
                                    user_id=get_effective_user_id(current_user)
                                )
                                db.add(exec_job)
                                ag.execucao_status = "pendente"
                                jobs_created += 1
                                
                # Se for IPASGO (6), criamos APENAS a rotina de Execucao Direta independente
                if ag.id_convenio == 6:
                    from sqlalchemy import cast, String
                    existing_exec = db.query(Job).filter(
                        Job.rotina.in_(["Execução", "op4_confirma_guia"]),
                        Job.status.in_(["pending", "processing"]),
                        cast(Job.params, String).contains(str(ag.id_agendamento))
                    ).first()
 
                    if not existing_exec:
                        cart = db.query(Carteirinha).filter(
                            Carteirinha.carteirinha == ag.carteirinha, 
                            Carteirinha.id_convenio == ag.id_convenio,
                            Carteirinha.user_id == ag.user_id
                        ).first()
                        
                        if cart:
                            # Parametros Mínimos OP4 Ipasgo (numero_guia, sessoes_realizadas, carteira)
                            sessoes = getattr(req, "sessoes_realizadas", 1) # fallback seguro para 1 sessão
                            exec_params = {
                                "agendamento_id": ag.id_agendamento,
                                "numero_guia": ag.numero_guia or "",
                                "cod_procedimento_fat": ag.cod_procedimento_fat or "",
                                "sessoes_realizadas": sessoes,
                                "carteira": cart.carteirinha
                            }
                            exec_job = Job(
                                carteirinha_id=cart.id,
                                id_convenio=cart.id_convenio,
                                rotina="op4_confirma_guia",
                                status="pending",
                                depending_id=None,
                                params=json.dumps(exec_params),
                                user_id=get_effective_user_id(current_user)
                            )
                            db.add(exec_job)
                            ag.execucao_status = "pendente"
                            jobs_created += 1
 
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass
    return {"status": "success", "updated": len(req.ids), "jobs_created": jobs_created}

class BatchDeleteRequest(BaseModel):
    ids: List[int]

@router.delete("/batch")
def batch_delete(req: BatchDeleteRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    from collections import Counter
    from models import BaseGuia
    # Se não for admin, verificar posse dos agendamentos
    if not current_user.is_admin:
        count_ag = db.query(Agendamento).filter(
            Agendamento.id_agendamento.in_(req.ids),
            Agendamento.user_id == get_effective_user_id(current_user)
        ).count()
        if count_ag != len(req.ids):
            raise HTTPException(status_code=403, detail="Um ou mais agendamentos não pertencem ao seu usuário.")

    # Find all affected guias
    agendamentos = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).all()
    guia_counts = Counter([ag.numero_guia for ag in agendamentos if ag.numero_guia])
    
    # Manually restore balance
    for guia_str, count in guia_counts.items():
        db.query(BaseGuia).filter(BaseGuia.guia == guia_str).update({BaseGuia.saldo: BaseGuia.saldo + count})
        
    deleted = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).delete(synchronize_session=False)
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass
    return {"status": "success", "deleted": deleted}

class FaturarRequest(BaseModel):
    agendamento_ids: list[int]

@router.post("/faturar")
def trigger_faturamento(req: FaturarRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    # Se não for admin, verificar posse dos agendamentos
    if not current_user.is_admin:
        count_ag = db.query(Agendamento).filter(
            Agendamento.id_agendamento.in_(req.agendamento_ids),
            Agendamento.user_id == get_effective_user_id(current_user)
        ).count()
        if count_ag != len(req.agendamento_ids):
            raise HTTPException(status_code=403, detail="Um ou mais agendamentos não pertencem ao seu usuário.")

    agendamentos = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids)).all()
    if not agendamentos:
        raise HTTPException(status_code=404, detail="Nenhum agendamento encontrado")
        
    from models import Job, Convenio
    from datetime import datetime
    import json
    
    jobs_created = []
    for agenda in agendamentos:
        cart = db.query(Carteirinha).filter(
            Carteirinha.carteirinha == agenda.carteirinha, 
            Carteirinha.id_convenio == agenda.id_convenio,
            Carteirinha.user_id == agenda.user_id
        ).first()
        
        if cart:
            # Anápolis (id_convenio=2): cria par Captura + Execução dependente
            if agenda.id_convenio == 2:
                # Anti-duplicidade para Captura
                cap_job = None
                if agenda.numero_guia:
                    cap_job = db.query(Job).filter(
                        Job.id_convenio == 2,
                        Job.rotina.in_(["Captura", "op2_captura"]),
                        Job.status.in_(["pending", "processing", "success"]),
                        Job.params.contains(agenda.numero_guia)
                    ).first()
                
                if not cap_job:
                    cap_job = Job(
                        carteirinha_id=cart.id,
                        id_convenio=cart.id_convenio,
                        rotina="op2_captura",
                        status="pending",
                        params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""}),
                        user_id=get_effective_user_id(current_user)
                    )
                    db.add(cap_job)
                    db.flush()
                
                jobs_created.append(cap_job.id)

                # Verifica se já existe Execução para este agendamento
                existing_exec = db.query(Job).filter(
                    Job.rotina.in_(["Execução", "op3_execucao"]),
                    Job.status.in_(["pending", "processing"]),
                    cast(Job.params, String).contains(str(agenda.id_agendamento))
                ).first()

                if not existing_exec:
                    # Enriquece params de Execução para Anápolis
                    prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == agenda.Id_profissional).first()
                    data_hora = ""
                    try:
                        if agenda.data and agenda.hora_inicio:
                            hora = agenda.hora_inicio
                            if isinstance(hora, str):
                                try: hora = datetime.strptime(hora[:5], "%H:%M").time()
                                except: pass
                            data_hora = f"{agenda.data.strftime('%d/%m/%Y')} {hora.strftime('%H:%M')}"
                    except: pass

                    exec_params = {
                        "agendamento_id": agenda.id_agendamento,
                        "numero_guia": agenda.numero_guia or "",
                        "nome_profissional": prof.nome if prof else (agenda.Nome_profissional or ""),
                        "conselho": prof.conselho if prof else "",
                        "data_hora": data_hora,
                        "cod_procedimento_fat": agenda.cod_procedimento_fat or ""
                    }
                    exec_job = Job(
                        carteirinha_id=cart.id,
                        id_convenio=cart.id_convenio,
                        rotina="op3_execucao",
                        status="pending",
                        depending_id=cap_job.id,
                        params=json.dumps(exec_params),
                        user_id=get_effective_user_id(current_user)
                    )
                    db.add(exec_job)
                    db.flush()
                    jobs_created.append(exec_job.id)
                    agenda.execucao_status = "pendente"
                else:
                    jobs_created.append(existing_exec.id)
            else:
                # Demais convênios: Faturamento direto
                new_job = Job(
                    carteirinha_id=cart.id,
                    id_convenio=cart.id_convenio,
                    rotina="Faturamento",
                    status="pending",
                    params=json.dumps({"origem": "batch_agendamentos", "agendamento_id": agenda.id_agendamento}),
                    user_id=get_effective_user_id(current_user)
                )
                db.add(new_job)
                db.flush()
                jobs_created.append(new_job.id)
            
            agenda.Status = "Faturamento Solicitado"
            
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "success", "message": f"{len(jobs_created)} Jobs criados", "jobs": jobs_created}

class AgendamentoJobRequest(BaseModel):
    agendamento_id: int
    depending_id: Optional[int] = None
    sessoes_realizadas: Optional[int] = 1

@router.post("/capturar")
def create_job_captura(req: AgendamentoJobRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    agenda = db.query(Agendamento).filter(Agendamento.id_agendamento == req.agendamento_id).first()
    if not agenda:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
    if not current_user.is_admin and agenda.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este agendamento.")

    cart = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == agenda.carteirinha, 
        Carteirinha.id_convenio == agenda.id_convenio,
        Carteirinha.user_id == agenda.user_id
    ).first()
    
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha vinculada não encontrada")
        
    from models import Job
    import json
    
    # Anti-duplicidade: verifica se já existe Captura pendente/processing/success para esta guia
    if agenda.numero_guia:
        existing = db.query(Job).filter(
            Job.id_convenio == agenda.id_convenio,
            Job.rotina.in_(["Captura", "op2_captura"]),
            Job.status.in_(["pending", "processing", "success"]),
            Job.params.contains(agenda.numero_guia)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409, 
                detail=f"Já existe job de Captura ativo para guia {agenda.numero_guia} (Job #{existing.id}, status={existing.status})"
            )
    
    new_job = Job(
        carteirinha_id=cart.id,
        id_convenio=agenda.id_convenio,
        rotina="op2_captura",
        status="pending",
        params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""}),
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    return {"status": "success", "job_id": new_job.id}

@router.post("/executar")
def create_job_execucao(req: AgendamentoJobRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Cria Job Execução. Para Goiânia/Anápolis, auto-cria Captura antes se necessário."""
    agenda = db.query(Agendamento).filter(Agendamento.id_agendamento == req.agendamento_id).first()
    if not agenda:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
    if not current_user.is_admin and agenda.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este agendamento.")

    cart = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == agenda.carteirinha, 
        Carteirinha.id_convenio == agenda.id_convenio,
        Carteirinha.user_id == agenda.user_id
    ).first()
    
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha vinculada não encontrada")
        
    from models import Job
    import json

    # Params base
    params_base = {"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""}

    # Para Anápolis (id_convenio=2): enriquece params com dados de execução SP/SADT
    if agenda.id_convenio == 2:
        prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == agenda.Id_profissional).first()
        data_hora = ""
        try:
            if agenda.data and agenda.hora_inicio:
                # hora_inicio pode ser datetime.time ou string "HH:MM:SS"
                hora = agenda.hora_inicio
                if isinstance(hora, str):
                    hora = datetime.strptime(hora[:5], "%H:%M").time()
                data_hora = f"{agenda.data.strftime('%d/%m/%Y')} {hora.strftime('%H:%M')}"
        except Exception:
            data_hora = ""
        params_base.update({
            "nome_profissional": prof.nome if prof else (agenda.Nome_profissional or ""),
            "conselho":          prof.conselho if prof else "",
            "data_hora":         data_hora,
            "cod_procedimento_fat": agenda.cod_procedimento_fat or ""
        })

    # Para IPASGO (id_convenio=6): enriquece params com carteira, procedimento, sessoes_realizadas
    if agenda.id_convenio == 6:
        sessoes_req = req.sessoes_realizadas if getattr(req, "sessoes_realizadas", None) is not None else 1
        params_base.update({
            "carteira": cart.carteirinha,
            "cod_procedimento_fat": agenda.cod_procedimento_fat or "",
            "sessoes_realizadas": sessoes_req
        })

    params_json = json.dumps(params_base)
    cap_job_id = req.depending_id  # fallback se já fornecido
    
    # Para Goiânia (3) e Anápolis (2): auto-cria Captura se não existe ainda
    if agenda.id_convenio in (2, 3) and not req.depending_id:
        # Verifica se já existe Captura com sucesso → usa como dependência
        existing_cap = None
        if agenda.numero_guia:
            from sqlalchemy import cast, String
            existing_cap = db.query(Job).filter(
                Job.id_convenio == agenda.id_convenio,
                Job.rotina.in_(["Captura", "op2_captura"]),
                Job.status.in_(["pending", "processing", "success"]),
                cast(Job.params, String).contains(agenda.numero_guia)
            ).first()
        
        if existing_cap:
            cap_job_id = existing_cap.id
        else:
            # Cria Captura standalone primeiro
            cap_job = Job(
                carteirinha_id=cart.id,
                id_convenio=agenda.id_convenio,
                rotina="op2_captura",
                status="pending",
                params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""}),
                user_id=get_effective_user_id(current_user)
            )
            db.add(cap_job)
            db.flush()
            cap_job_id = cap_job.id
    
    
    # Anti-duplicidade Execução
    from sqlalchemy import cast, String
    exec_rotina = "op3_execucao" if agenda.id_convenio == 2 else "op4_confirma_guia" if agenda.id_convenio == 6 else "op3_execucao"
    
    existing_exec = db.query(Job).filter(
        Job.rotina.in_(["Execução", "op3_execucao", "op4_confirma_guia"]),
        Job.status.in_(["pending", "processing"]),
        cast(Job.params, String).contains(str(agenda.id_agendamento))
    ).first()

    if existing_exec:
        return {"status": "success", "message": "Job de execução já existente", "job_id": existing_exec.id}

    # Para IPASGO (6), set depending_id para None explicitamente se nao tiver id injetado   
    if agenda.id_convenio == 6:
        cap_job_id = None

    new_job = Job(
        carteirinha_id=cart.id,
        id_convenio=agenda.id_convenio,
        rotina=exec_rotina,
        status="pending",
        depending_id=cap_job_id,
        params=params_json,
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    
    agenda.execucao_status = "pendente"
    
    db.commit()
    db.refresh(new_job)
    return {"status": "success", "job_id": new_job.id, "captura_job_id": cap_job_id}


@router.post("/executar")
def create_job_execucao(req: AgendamentoJobRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Cria Job Execução. Para Goiânia/Anápolis, auto-cria Captura antes se necessário."""
    agenda = db.query(Agendamento).filter(Agendamento.id_agendamento == req.agendamento_id).first()
    if not agenda:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
    if not current_user.is_admin and agenda.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este agendamento.")

    cart = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == agenda.carteirinha, 
        Carteirinha.id_convenio == agenda.id_convenio,
        Carteirinha.user_id == agenda.user_id
    ).first()
    
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha vinculada não encontrada")
        
    from models import Job
    import json

    # Params base
    params_base = {"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""}

    # Para Anápolis (id_convenio=2): enriquece params com dados de execução SP/SADT
    if agenda.id_convenio == 2:
        prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == agenda.Id_profissional).first()
        data_hora = ""
        try:
            if agenda.data and agenda.hora_inicio:
                # hora_inicio pode ser datetime.time ou string "HH:MM:SS"
                hora = agenda.hora_inicio
                if isinstance(hora, str):
                    hora = datetime.strptime(hora[:5], "%H:%M").time()
                data_hora = f"{agenda.data.strftime('%d/%m/%Y')} {hora.strftime('%H:%M')}"
        except Exception:
            data_hora = ""
        params_base.update({
            "nome_profissional": prof.nome if prof else (agenda.Nome_profissional or ""),
            "conselho":          prof.conselho if prof else "",
            "data_hora":         data_hora,
            "cod_procedimento_fat": agenda.cod_procedimento_fat or ""
        })

    # Para IPASGO (id_convenio=6): enriquece params com carteira, procedimento, sessoes_realizadas
    if agenda.id_convenio == 6:
        sessoes_req = req.sessoes_realizadas if getattr(req, "sessoes_realizadas", None) is not None else 1
        params_base.update({
            "carteira": cart.carteirinha,
            "cod_procedimento_fat": agenda.cod_procedimento_fat or "",
            "sessoes_realizadas": sessoes_req
        })

    params_json = json.dumps(params_base)
    cap_job_id = req.depending_id  # fallback se já fornecido
    
    # Para Goiânia (3) e Anápolis (2): auto-cria Captura se não existe ainda
    if agenda.id_convenio in (2, 3) and not req.depending_id:
        # Verifica se já existe Captura com sucesso → usa como dependência
        existing_cap = None
        if agenda.numero_guia:
            from sqlalchemy import cast, String
            existing_cap = db.query(Job).filter(
                Job.id_convenio == agenda.id_convenio,
                Job.rotina.in_(["Captura", "op2_captura"]),
                Job.status.in_(["pending", "processing", "success"]),
                cast(Job.params, String).contains(agenda.numero_guia)
            ).first()
        
        if existing_cap:
            cap_job_id = existing_cap.id
        else:
            # Cria Captura standalone primeiro
            cap_job = Job(
                carteirinha_id=cart.id,
                id_convenio=agenda.id_convenio,
                rotina="op2_captura",
                status="pending",
                params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""}),
                user_id=get_effective_user_id(current_user)
            )
            db.add(cap_job)
            db.flush()
            cap_job_id = cap_job.id
    
    
    # Anti-duplicidade Execução
    from sqlalchemy import cast, String
    exec_rotina = "op3_execucao" if agenda.id_convenio == 2 else "op4_confirma_guia" if agenda.id_convenio == 6 else "op3_execucao"
    
    existing_exec = db.query(Job).filter(
        Job.rotina.in_(["Execução", "op3_execucao", "op4_confirma_guia"]),
        Job.status.in_(["pending", "processing"]),
        cast(Job.params, String).contains(str(agenda.id_agendamento))
    ).first()

    if existing_exec:
        return {"status": "success", "message": "Job de execução já existente", "job_id": existing_exec.id}

    # Para IPASGO (6), set depending_id para None explicitamente se nao tiver id injetado   
    if agenda.id_convenio == 6:
        cap_job_id = None

    new_job = Job(
        carteirinha_id=cart.id,
        id_convenio=agenda.id_convenio,
        rotina=exec_rotina,
        status="pending",
        depending_id=cap_job_id,
        params=params_json,
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    
    agenda.execucao_status = "pendente"
    
    db.commit()
    db.refresh(new_job)
    return {"status": "success", "job_id": new_job.id, "captura_job_id": cap_job_id}


@router.get("/profissionais")
def list_profissionais(
    tipo: Optional[str] = None,
    search: Optional[str] = None,
    skip: Optional[int] = None,
    limit: Optional[int] = None,
    page: Optional[int] = None,
    pageSize: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Retorna a lista de profissionais ativos do corpo clínico com suporte a cache Two-Tier, busca e paginação."""
    target_id = get_effective_user_id(current_user)
    
    # Calcular skip e limit se page/pageSize foram passados
    actual_limit = limit if limit is not None else (pageSize if pageSize is not None else None)
    actual_skip = skip if skip is not None else (((page - 1) * actual_limit) if (page is not None and actual_limit is not None) else None)
    
    cache_params = {
        "tipo": tipo,
        "search": search,
        "skip": actual_skip,
        "limit": actual_limit
    }
    
    from cache import cache
    cached_res = cache.get(target_id, "profissionais", cache_params)
    if cached_res:
        return cached_res

    query = db.query(CorpoClinico).filter(CorpoClinico.status == "ativo")
    if tipo:
        query = query.filter(CorpoClinico.tipo_profissional == tipo)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                CorpoClinico.nome.ilike(s),
                CorpoClinico.cpf.ilike(s),
                CorpoClinico.area.ilike(s),
                CorpoClinico.registro.ilike(s),
                CorpoClinico.codigo_ipasgo.ilike(s)
            )
        )

    # Não-admin vê seus próprios profissionais OU registros globais (user_id IS NULL)
    # Médicos (tipo_profissional == 'medico') são sempre livres para todos os usuários
    if not current_user.is_admin:
        query = query.filter(
            (CorpoClinico.tipo_profissional == "medico") |
            (CorpoClinico.user_id == current_user.id) |
            (CorpoClinico.user_id.is_(None))
        )
    
    total = query.count()
    
    order_query = query.order_by(CorpoClinico.nome)
    if actual_skip is not None:
        order_query = order_query.offset(actual_skip)
    if actual_limit is not None:
        order_query = order_query.limit(actual_limit)
        
    profissionais = order_query.all()

    items = [
        {
            "id_profissional": p.id_profissional,
            "nome": p.nome,
            "cpf": p.cpf or "",
            "area": p.area or "",
            "conselho": p.conselho or "",
            "registro": p.registro or "",
            "UF": p.UF or "",
            "CBO": p.CBO or "",
            "codigo_ipasgo": p.codigo_ipasgo or "",
            "tipo_profissional": p.tipo_profissional or "profissional"
        }
        for p in profissionais
    ]

    # Se paginação foi solicitada, retorna objeto com data e total. Se não, lista simples compatível
    if actual_limit is not None or page is not None:
        result = {
            "data": items,
            "total": total,
            "page": page or (actual_skip // actual_limit + 1 if actual_limit else 1),
            "pageSize": actual_limit or total
        }
    else:
        result = items

    try:
        cache.set(target_id, "profissionais", cache_params, result, ttl=120)
    except Exception:
        pass

    return result


class ProfissionalCreateSchema(BaseModel):
    nome: str
    cpf: Optional[str] = None
    area: Optional[str] = None
    conselho: Optional[str] = None
    registro: Optional[str] = None
    UF: Optional[str] = None
    CBO: Optional[str] = None
    codigo_ipasgo: Optional[str] = None
    tipo_profissional: Optional[str] = "profissional"


@router.post("/profissionais")
def create_profissional(
    req: ProfissionalCreateSchema,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    new_prof = CorpoClinico(
        nome=req.nome,
        cpf=req.cpf,
        area=req.area,
        conselho=req.conselho,
        registro=req.registro,
        UF=req.UF,
        CBO=req.CBO,
        codigo_ipasgo=req.codigo_ipasgo,
        tipo_profissional=req.tipo_profissional,
        status="ativo",
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_prof)
    db.commit()
    db.refresh(new_prof)
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass
    return {
        "status": "success",
        "id_profissional": new_prof.id_profissional,
        "profissional": {
            "id_profissional": new_prof.id_profissional,
            "nome": new_prof.nome,
            "cpf": new_prof.cpf or "",
            "area": new_prof.area or "",
            "conselho": new_prof.conselho,
            "registro": new_prof.registro,
            "UF": new_prof.UF,
            "CBO": new_prof.CBO,
            "codigo_ipasgo": new_prof.codigo_ipasgo,
            "tipo_profissional": new_prof.tipo_profissional
        }
    }


@router.put("/profissionais/{id_profissional}")
def update_profissional(
    id_profissional: str,
    req: ProfissionalCreateSchema,
    area: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == id_profissional)
    if area:
        query = query.filter(CorpoClinico.area == area)
    prof = query.first()
    if not prof:
        raise HTTPException(status_code=404, detail="Profissional não encontrado.")
    
    if not current_user.is_admin and prof.user_id != current_user.id and prof.user_id is not None:
        raise HTTPException(status_code=403, detail="Sem permissão para editar este profissional.")
        
    prof.nome = req.nome
    prof.cpf = req.cpf
    prof.area = req.area
    prof.conselho = req.conselho
    prof.registro = req.registro
    prof.UF = req.UF
    prof.CBO = req.CBO
    prof.codigo_ipasgo = req.codigo_ipasgo
    prof.tipo_profissional = req.tipo_profissional
    
    db.commit()
    db.refresh(prof)
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass
    return {"status": "success", "id_profissional": prof.id_profissional}


@router.delete("/profissionais/{id_profissional}")
def delete_profissional(
    id_profissional: str,
    area: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == id_profissional)
    if area:
        query = query.filter(CorpoClinico.area == area)
    prof = query.first()
    if not prof:
        raise HTTPException(status_code=404, detail="Profissional não encontrado.")
        
    if not current_user.is_admin and prof.user_id != current_user.id and prof.user_id is not None:
        raise HTTPException(status_code=403, detail="Sem permissão para remover este profissional.")
        
    prof.status = "inativo"
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass
    return {"status": "success", "message": "Profissional desativado com sucesso."}


# ── Pipeline Workflow Endpoints ──

class ConfirmarPortalRequest(BaseModel):
    agendamento_ids: List[int]
    remover: bool = False

@router.post("/confirmar-portal")
def confirmar_portal(
    req: ConfirmarPortalRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria Job worker OP3 para confirmar/remover confirmação no portal ABA."""
    from models import Job, UserConvenio
    import os

    if not req.agendamento_ids:
        raise HTTPException(status_code=400, detail="Nenhum agendamento selecionado.")

    agendamentos = db.query(Agendamento).filter(
        Agendamento.id_agendamento.in_(req.agendamento_ids)
    ).all()
    if not agendamentos:
        raise HTTPException(status_code=404, detail="Agendamentos não encontrados.")

    if not current_user.is_admin:
        for ag in agendamentos:
            if ag.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Sem permissão para este agendamento.")

    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == current_user.id,
        UserConvenio.id_convenio == 101
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(status_code=400, detail="Credenciais ABA CLMF não configuradas.")

    portal_ids = [ag.id_agendamento for ag in agendamentos]
    num_situacao = 0 if req.remover else 1

    params_dict = {
        "id_agendamento": portal_ids,
        "num_situacao": num_situacao,
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "cod_prestador": uconv.cod_prestador,
        "webhook_url": os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")
    }

    new_job = Job(
        id_convenio=101,
        rotina="op3_confirmar_agendamento",
        status="pending",
        params=params_dict,
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    # Set execucao_status to processando while preserving current stage for spinner UI
    db.query(Agendamento).filter(Agendamento.id_agendamento.in_(portal_ids)).update({
        Agendamento.execucao_status: "processando"
    }, synchronize_session=False)
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass

    action = "remoção de confirmação" if req.remover else "confirmação"
    return {
        "status": "success",
        "message": f"Job #{new_job.id} de {action} criado para {len(portal_ids)} agendamento(s).",
        "job_id": new_job.id
    }


class RegistrarFaltaPortalRequest(BaseModel):
    agendamento_ids: List[int]
    id_paciente: int
    motivo_falta_id: int
    doc_justificativa: Optional[str] = None

@router.post("/registrar-falta-portal")
def registrar_falta_portal(
    req: RegistrarFaltaPortalRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria Job worker OP4 para registrar falta no portal ABA."""
    from models import Job, UserConvenio, MotivoFalta
    import json, os

    if not req.agendamento_ids:
        raise HTTPException(status_code=400, detail="Nenhum agendamento selecionado.")

    motivo = db.query(MotivoFalta).filter(MotivoFalta.id == req.motivo_falta_id).first()
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo de falta não encontrado.")

    agendamentos = db.query(Agendamento).filter(
        Agendamento.id_agendamento.in_(req.agendamento_ids)
    ).all()
    if not current_user.is_admin:
        for ag in agendamentos:
            if ag.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Sem permissão para este agendamento.")

    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == current_user.id,
        UserConvenio.id_convenio == 101
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(status_code=400, detail="Credenciais ABA CLMF não configuradas.")

    portal_ids = [ag.id_agendamento for ag in agendamentos]

    params_dict = {
        "id_agendamento": portal_ids,
        "id_paciente": req.id_paciente,
        "tipo_desagendamento": motivo.id_mapeado,
        "doc_justificativa": req.doc_justificativa or "",
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "cod_prestador": uconv.cod_prestador,
        "webhook_url": os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")
    }

    new_job = Job(
        id_convenio=101,
        rotina="op4_registrar_falta",
        status="pending",
        params=params_dict,
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids)).update({
        Agendamento.execucao_status: "processando"
    }, synchronize_session=False)
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass

    return {
        "status": "success",
        "message": f"Job #{new_job.id} de registro de falta criado para {len(portal_ids)} agendamento(s).",
        "job_id": new_job.id,
        "motivo": motivo.descricao
    }


class RemoverFaltaPortalRequest(BaseModel):
    agendamento_ids: List[int]
    id_paciente: int
    data_inicial: Optional[str] = None
    data_final: Optional[str] = None

@router.post("/remover-falta-portal")
def remover_falta_portal(
    req: RemoverFaltaPortalRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria Job worker OP5 para remover falta no portal ABA."""
    from models import Job, UserConvenio
    import os
    from datetime import date as _date

    if not req.agendamento_ids:
        raise HTTPException(status_code=400, detail="Nenhum agendamento selecionado.")

    agendamentos = db.query(Agendamento).filter(
        Agendamento.id_agendamento.in_(req.agendamento_ids)
    ).all()
    if not current_user.is_admin:
        for ag in agendamentos:
            if ag.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Sem permissão para este agendamento.")

    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == current_user.id,
        UserConvenio.id_convenio == 101
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(status_code=400, detail="Credenciais ABA CLMF não configuradas.")

    portal_ids = [ag.id_agendamento for ag in agendamentos]
    today_str = _date.today().strftime("%Y-%m-%d")

    params_dict = {
        "id_agendamento": portal_ids,
        "id_paciente": req.id_paciente,
        "data_inicial": req.data_inicial or today_str,
        "data_final": req.data_final or today_str,
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "cod_prestador": uconv.cod_prestador,
        "webhook_url": os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")
    }

    new_job = Job(
        id_convenio=101,
        rotina="op5_remover_falta",
        status="pending",
        params=params_dict,
        user_id=get_effective_user_id(current_user)
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids)).update({
        Agendamento.execucao_status: "processando"
    }, synchronize_session=False)
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(get_effective_user_id(current_user))
    except Exception:
        pass

class ImprimirIpasgoRequest(BaseModel):
    agendamento_id: int

@router.post("/imprimir-ipasgo")
def gerar_op_impressao_ipasgo(
    req: ImprimirIpasgoRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    worker_key: Optional[str] = Depends(get_current_worker_key)
):
    ag = db.query(Agendamento).filter(Agendamento.id_agendamento == req.agendamento_id).first()
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    if not current_user.is_admin and ag.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Acesso negado.")

    new_job = Job(
        id_convenio=ag.id_convenio or 6,
        rotina="12", # OP12 Worker Impressão
        status="pending",
        user_id=get_effective_user_id(current_user),
        worker_key=worker_key,
        params={
            "guia": ag.numero_guia or "",
            "GuiaPrestador": ag.numero_guia or "",
            "id_agendamento": ag.id_agendamento,
            "numero_copias": 1
        }
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    return {"status": "success", "message": f"Job OP12 de impressão IPASGO #{new_job.id} enfileirado com sucesso!", "job_id": new_job.id}

