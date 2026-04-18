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
from sqlalchemy import func, String, cast

router = APIRouter(
    prefix="/agendamentos",
    tags=["Agendamentos"]
)

class CreateAgendamentoRequest(BaseModel):
    carteirinha: str
    id_convenio: int
    Id_profissional: int
    cod_procedimento_aut: str
    data: date
    hora_inicio: time
    sala: Optional[str] = None
    Tipo_atendimento: str
    Status: str = "A Confirmar"

@router.post("/")
def create_agendamento(req: CreateAgendamentoRequest, db: Session = Depends(get_db)):
    # 1. Load dependencies based on input
    
    # Check Carteirinha
    cart = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == req.carteirinha,
        Carteirinha.id_convenio == req.id_convenio
    ).first()
    
    if not cart:
        # Tenta buscar ignorando o convenio, caso seja cart universal ou errada
        cart = db.query(Carteirinha).filter(Carteirinha.carteirinha == req.carteirinha).first()
        if not cart:
            raise HTTPException(status_code=404, detail="Carteirinha não encontrada.")

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
        Status=req.Status
    )

    try:
        db.add(new_agendamento)
        db.commit()
        db.refresh(new_agendamento)
        return {"status": "success", "agendamento": new_agendamento}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/vincular-guias")
def vincular_guias_manualmente(db: Session = Depends(get_db)):
    """
    Desperta deliberadamente o Trigger Mestre das Guias.
    Somente guias ainda válidas (com saldo) serão "puxadas" acordando a tabela, 
    vasculhando qualquer Agendamento orfão pendente na agenda elegível para descontos.
    """
    try:
        updated = db.query(BaseGuia).filter(
            BaseGuia.saldo > 0,
            BaseGuia.status_guia.notin_(['Cancelada', 'Negada'])
        ).update({
            BaseGuia.updated_at: func.now()
        }, synchronize_session=False)
        db.commit()
        return {"status": "success", "message": f"Varredura de Guias ativada. Lotes com saldo positivo reagiram ao pulso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao forçar vinculação de guias: {str(e)}")

@router.get("/")
def list_agendamentos(
    paciente: Optional[str] = None,
    id_convenio: Optional[int] = None,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    status: Optional[str] = None,
    procedimento: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db)
):
    query = db.query(Agendamento)
    
    if paciente:
        query = query.filter(Agendamento.Nome_Paciente.ilike(f"%{paciente}%"))
    if id_convenio:
        query = query.filter(Agendamento.id_convenio == id_convenio)
    if data_inicio:
        query = query.filter(Agendamento.data >= data_inicio)
    if data_fim:
        query = query.filter(Agendamento.data <= data_fim)
    if status:
        query = query.filter(Agendamento.Status == status)
    if procedimento:
        query = query.filter(Agendamento.nome_procedimento.ilike(f"%{procedimento}%"))
        
    total = query.count()
    # Joined loading of gui? We can do a join to return saldo, but since this relies on front-end, let's keep it direct.
    # We will fetch 'saldo' as an annotation if numero_guia matches.
    
    # Outer join to fetch the Saldo da Guia if numero_guia is populated
    from sqlalchemy.orm import aliased
    from models import BaseGuia
    bg = aliased(BaseGuia)
    
    agendamentos = (
        db.query(Agendamento, bg.saldo.label("saldo_guia"), bg.timestamp_captura.label("timestamp_captura"))
        .outerjoin(bg, Agendamento.numero_guia == bg.guia)
        .filter(*query.whereclause.clauses if hasattr(query.whereclause, 'clauses') else [query.whereclause] if query.whereclause is not None else [])
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
        data.append(dic)
        
    total_db_unfiltered = db.query(Agendamento).count()
        
    confirmados = query.filter(Agendamento.Status == 'Confirmado').count()
    a_confirmar = query.filter(Agendamento.Status == 'A Confirmar').count()
    faltas = query.filter(Agendamento.Status == 'Falta').count()
        
    return {
        "data": data, 
        "total": total, 
        "total_geral": total_db_unfiltered, 
        "skip": skip, 
        "limit": limit,
        "kpis": {
            "confirmados": confirmados,
            "a_confirmar": a_confirmar,
            "faltas": faltas
        }
    }

@router.get("/procedimentos")
def list_procedimentos(id_convenio: int, db: Session = Depends(get_db)):
    procs = db.query(Procedimento.nome)\
              .filter(Procedimento.id_convenio == id_convenio)\
              .distinct().all()
    # Retorna array flat
    return [p[0] for p in procs if p[0] is not None]

class BatchStatusRequest(BaseModel):
    ids: List[int]
    status: str
    capturar_guias: bool = True

@router.put("/batch-status")
def batch_update_status(req: BatchStatusRequest, db: Session = Depends(get_db)):
    from collections import Counter
    from models import BaseGuia

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
                        Carteirinha.id_convenio == ag.id_convenio
                    ).first()
                    if cart:
                        # 1. Busca ou Cria Captura
                        cap_job = None
                        if ag.numero_guia:
                            from sqlalchemy import cast, String
                            cap_job = db.query(Job).filter(
                                Job.id_convenio == ag.id_convenio,
                                Job.rotina == "Captura",
                                Job.status.in_(["pending", "processing", "success"]),
                                cast(Job.params, String).contains(ag.numero_guia)
                            ).first()
                        
                        if not cap_job:
                            cap_job = Job(
                                carteirinha_id=cart.id,
                                id_convenio=ag.id_convenio,
                                rotina="Captura",
                                status="pending",
                                params=json.dumps({"agendamento_id": ag.id_agendamento, "numero_guia": ag.numero_guia or ""})
                            )
                            db.add(cap_job)
                            db.flush()
                            jobs_created += 1

                        # 2. Para Anápolis (2), cria também a Execução dependente se não existir
                        if ag.id_convenio == 2:
                            from sqlalchemy import cast, String
                            existing_exec = db.query(Job).filter(
                                Job.rotina == "Execução",
                                Job.status.in_(["pending", "processing"]),
                                cast(Job.params, String).contains(f'"agendamento_id": {ag.id_agendamento}')
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
                                    rotina="Execução",
                                    status="pending",
                                    depending_id=cap_job.id,
                                    params=json.dumps(exec_params)
                                )
                                db.add(exec_job)
                                ag.execucao_status = "pendente"
                                jobs_created += 1
                                
                # Se for IPASGO (6), criamos APENAS a rotina de Execucao Direta independente
                if ag.id_convenio == 6:
                    from sqlalchemy import cast, String
                    existing_exec = db.query(Job).filter(
                        Job.rotina == "Execução",
                        Job.status.in_(["pending", "processing"]),
                        cast(Job.params, String).contains(f'"agendamento_id": {ag.id_agendamento}')
                    ).first()

                    if not existing_exec:
                        cart = db.query(Carteirinha).filter(
                            Carteirinha.carteirinha == ag.carteirinha, 
                            Carteirinha.id_convenio == ag.id_convenio
                        ).first()
                        
                        if cart:
                            # Parametros Mínimos OP4 Ipasgo (numero_guia, sessoes_realizadas)
                            sessoes = getattr(req, "sessoes_realizadas", 1) # fallback seguro para 1 sessão
                            exec_params = {
                                "agendamento_id": ag.id_agendamento,
                                "numero_guia": ag.numero_guia or "",
                                "cod_procedimento_fat": ag.cod_procedimento_fat or "",
                                "sessoes_realizadas": sessoes
                            }
                            exec_job = Job(
                                carteirinha_id=cart.id,
                                id_convenio=cart.id_convenio,
                                rotina="Execução",
                                status="pending",
                                depending_id=None,
                                params=json.dumps(exec_params)
                            )
                            db.add(exec_job)
                            ag.execucao_status = "pendente"
                            jobs_created += 1

    db.commit()
    return {"status": "success", "updated": len(req.ids), "jobs_created": jobs_created}

class BatchDeleteRequest(BaseModel):
    ids: List[int]

@router.delete("/batch")
def batch_delete(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    from collections import Counter
    from models import BaseGuia
    # Find all affected guias
    agendamentos = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).all()
    guia_counts = Counter([ag.numero_guia for ag in agendamentos if ag.numero_guia])
    
    # Manually restore balance
    for guia_str, count in guia_counts.items():
        db.query(BaseGuia).filter(BaseGuia.guia == guia_str).update({BaseGuia.saldo: BaseGuia.saldo + count})
        
    deleted = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.ids)).delete(synchronize_session=False)
    db.commit()
    return {"status": "success", "deleted": deleted}

class FaturarRequest(BaseModel):
    agendamento_ids: list[int]

@router.post("/faturar")
def trigger_faturamento(req: FaturarRequest, db: Session = Depends(get_db)):
    agendamentos = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids)).all()
    if not agendamentos:
        raise HTTPException(status_code=404, detail="Nenhum agendamento encontrado")
        
    from models import Job, Convenio
    from datetime import datetime
    import json
    
    jobs_created = []
    for agenda in agendamentos:
        cart = db.query(Carteirinha).filter(Carteirinha.carteirinha == agenda.carteirinha, Carteirinha.id_convenio == agenda.id_convenio).first()
        
        if cart:
            # Anápolis (id_convenio=2): cria par Captura + Execução dependente
            if agenda.id_convenio == 2:
                # Anti-duplicidade para Captura
                cap_job = None
                if agenda.numero_guia:
                    cap_job = db.query(Job).filter(
                        Job.id_convenio == 2,
                        Job.rotina == "Captura",
                        Job.status.in_(["pending", "processing", "success"]),
                        Job.params.contains(agenda.numero_guia)
                    ).first()
                
                if not cap_job:
                    cap_job = Job(
                        carteirinha_id=cart.id,
                        id_convenio=cart.id_convenio,
                        rotina="Captura",
                        status="pending",
                        params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""})
                    )
                    db.add(cap_job)
                    db.flush()
                
                jobs_created.append(cap_job.id)

                # Verifica se já existe Execução para este agendamento
                existing_exec = db.query(Job).filter(
                    Job.rotina == "Execução",
                    Job.status.in_(["pending", "processing"]),
                    Job.params.contains(f'"agendamento_id": {agenda.id_agendamento}')
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
                        rotina="Execução",
                        status="pending",
                        depending_id=cap_job.id,
                        params=json.dumps(exec_params)
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
                    params=json.dumps({"origem": "batch_agendamentos", "agendamento_id": agenda.id_agendamento})
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

@router.post("/capturar")
def create_job_captura(req: AgendamentoJobRequest, db: Session = Depends(get_db)):
    agenda = db.query(Agendamento).filter(Agendamento.id_agendamento == req.agendamento_id).first()
    if not agenda:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
    cart = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == agenda.carteirinha, 
        Carteirinha.id_convenio == agenda.id_convenio
    ).first()
    
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha vinculada não encontrada")
        
    from models import Job
    import json
    
    # Anti-duplicidade: verifica se já existe Captura pendente/processing/success para esta guia
    if agenda.numero_guia:
        existing = db.query(Job).filter(
            Job.id_convenio == agenda.id_convenio,
            Job.rotina == "Captura",
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
        rotina="Captura",
        status="pending",
        params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""})
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    return {"status": "success", "job_id": new_job.id}

@router.post("/executar")
def create_job_execucao(req: AgendamentoJobRequest, db: Session = Depends(get_db)):
    """Cria Job Execução. Para Goiânia/Anápolis, auto-cria Captura antes se necessário."""
    agenda = db.query(Agendamento).filter(Agendamento.id_agendamento == req.agendamento_id).first()
    if not agenda:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
    cart = db.query(Carteirinha).filter(
        Carteirinha.carteirinha == agenda.carteirinha, 
        Carteirinha.id_convenio == agenda.id_convenio
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
                Job.rotina == "Captura",
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
                rotina="Captura",
                status="pending",
                params=json.dumps({"agendamento_id": agenda.id_agendamento, "numero_guia": agenda.numero_guia or ""})
            )
            db.add(cap_job)
            db.flush()
            cap_job_id = cap_job.id
    
    
    # Anti-duplicidade Execução
    from sqlalchemy import cast, String
    existing_exec = db.query(Job).filter(
        Job.rotina == "Execução",
        Job.status.in_(["pending", "processing"]),
        cast(Job.params, String).contains(f'"agendamento_id": {agenda.id_agendamento}')
    ).first()

    if existing_exec:
        return {"status": "success", "message": "Job de execução já existente", "job_id": existing_exec.id}

    # Para IPASGO (6), set depending_id para None explicitamente se nao tiver id injetado   
    if agenda.id_convenio == 6:
        cap_job_id = None

    new_job = Job(
        carteirinha_id=cart.id,
        id_convenio=agenda.id_convenio,
        rotina="Execução",
        status="pending",
        depending_id=cap_job_id,
        params=params_json
    )
    db.add(new_job)
    
    agenda.execucao_status = "pendente"
    
    db.commit()
    db.refresh(new_job)
    return {"status": "success", "job_id": new_job.id, "captura_job_id": cap_job_id}

