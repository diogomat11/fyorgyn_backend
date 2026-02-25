from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime

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
        db.query(Agendamento, bg.saldo.label("saldo_guia"))
        .outerjoin(bg, Agendamento.numero_guia == bg.guia)
        .filter(*query.whereclause.clauses if hasattr(query.whereclause, 'clauses') else [query.whereclause] if query.whereclause is not None else [])
        .order_by(Agendamento.data.desc().nulls_last(), Agendamento.hora_inicio.desc().nulls_last())
        .limit(limit)
        .offset(skip)
        .all()
    )
    
    # Format the response map
    data = []
    for ag, saldo in agendamentos:
        dic = {c.name: getattr(ag, c.name) for c in ag.__table__.columns}
        dic["saldo_guia"] = saldo
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
    
    db.commit()
    return {"status": "success", "updated": len(req.ids)}

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
        
    from models import Job
    from datetime import datetime
    
    jobs_created = []
    for agenda in agendamentos:
        # Pega a carteirinha
        cart = db.query(Carteirinha).filter(Carteirinha.carteirinha == agenda.carteirinha, Carteirinha.id_convenio == agenda.id_convenio).first()
        
        # O Faturamento (OP 3) eh criado para a base de carteirinha
        if cart:
            new_job = Job(
                carteirinha_id=cart.id,
                id_convenio=cart.id_convenio,
                rotina="Faturamento",
                status="Pendente",
                params='{"origem": "batch_agendamentos", "agendamento_id": ' + str(agenda.id_agendamento) + '}'
            )
            db.add(new_job)
            db.flush()
            jobs_created.append(new_job.id)
            
            # Atualiza status do Agendamento
            agenda.Status = "Faturamento Solicitado"
            
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "success", "message": f"{len(jobs_created)} Jobs de Faturamento criados", "jobs": jobs_created}
