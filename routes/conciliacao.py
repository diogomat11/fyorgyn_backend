from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Agendamento, FaturamentoLote, LoteConvenio, LoteAgendamento,
    LoteAgendamentoItem, Carteirinha, BaseGuia
)
from dependencies import get_current_user, get_allowed_convenio_ids
from typing import Optional, List
from pydantic import BaseModel
from datetime import date
from sqlalchemy import func

router = APIRouter(
    prefix="/conciliacao",
    tags=["conciliacao"]
)

# ── Schemas ──

class GerarLoteAgendamentoRequest(BaseModel):
    id_convenio: int
    data_inicio: date
    data_fim: date

class ConciliarRequest(BaseModel):
    id_lote_convenio: int  # id_lote da tabela lotes_convenio
    id_lote_ag: int        # id_lote_ag da tabela lotes_agendamento

class ConciliarManualRequest(BaseModel):
    id_faturamento_lote: int
    id_agendamento: int

class EditarItemRequest(BaseModel):
    dataRealizacao: Optional[date] = None
    Guia: Optional[str] = None
    cod_procedimento_fat: Optional[str] = None

# ── Helpers ──

def compute_status_verificacao(agendamento_data: date, guia: BaseGuia, numero_guia: str = None):
    """Verifica se a data do atendimento está dentro do período da guia."""
    if not guia:
        # Guia existe no agendamento mas não na tabela base_guias
        if numero_guia:
            return {"icone": "V", "texto": "Guia não cadastrada (validação indisponível)", "apto": True}
        return {"icone": "!", "texto": "Sem guia vinculada", "apto": False}
    
    if not guia.data_autorizacao or not guia.validade:
        return {"icone": "V", "texto": "Guia sem datas (validação indisponível)", "apto": True}
    
    if agendamento_data >= guia.data_autorizacao and agendamento_data <= guia.validade:
        return {"icone": "V", "texto": "Apto a faturar", "apto": True}
    else:
        return {"icone": "!", "texto": "Fora do período da guia", "apto": False}

def compute_saldo_exec(db: Session, guia_numero: str, id_lote: int):
    """Calcula saldo = itens_guia_no_lote - agendamentos_com_numero_guia."""
    qtd_itens_lote = db.query(FaturamentoLote).filter(
        FaturamentoLote.Guia == guia_numero,
        FaturamentoLote.id_lote == id_lote
    ).count()
    
    qtd_agendamentos = db.query(Agendamento).filter(
        Agendamento.numero_guia == guia_numero
    ).count()
    
    saldo = qtd_itens_lote - qtd_agendamentos
    
    if saldo < 0:
        return {"saldo": saldo, "icone": "X", "cor": "vermelho", "texto": "Execuções inferior"}
    elif saldo == 0:
        return {"saldo": saldo, "icone": "V", "cor": "verde", "texto": "Execuções OK"}
    else:
        return {"saldo": saldo, "icone": "!", "cor": "verde", "texto": "Execuções a maior"}

def resolve_codigo_beneficiario(db: Session, ag: Agendamento) -> Optional[str]:
    """Resolve codigo_beneficiario a partir do agendamento.
    Tenta id_carteirinha primeiro, depois fallback pelo campo texto 'carteirinha'.
    """
    if ag.id_carteirinha:
        cart = db.query(Carteirinha).filter(Carteirinha.id == ag.id_carteirinha).first()
        if cart and cart.codigo_beneficiario:
            return cart.codigo_beneficiario
    if ag.carteirinha:
        cart_val = ag.carteirinha.strip()
        cart = db.query(Carteirinha).filter(Carteirinha.carteirinha == cart_val).first()
        if cart and cart.codigo_beneficiario:
            return cart.codigo_beneficiario
    return None

# ── Endpoints: Lotes de Agendamento ──

@router.get("/lote-agendamentos")
def list_lotes_agendamento(
    id_convenio: Optional[int] = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    allowed_ids = get_allowed_convenio_ids(current_user)
    query = db.query(LoteAgendamento)
    
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        query = query.filter(LoteAgendamento.id_convenio == id_convenio)
    elif allowed_ids:
        query = query.filter(LoteAgendamento.id_convenio.in_(allowed_ids))
    
    total = query.count()
    lotes = query.order_by(LoteAgendamento.created_at.desc()).offset(skip).limit(limit).all()
    
    # Buscar contadores em batch ao invés de N+1
    lote_ids = [l.id_lote_ag for l in lotes]
    
    totals_q = (
        db.query(
            LoteAgendamentoItem.id_lote_ag,
            func.count(LoteAgendamentoItem.id).label("total"),
            func.count(func.nullif(LoteAgendamentoItem.status_conciliacao != 'Conciliado', True)).label("conciliados")
        )
        .filter(LoteAgendamentoItem.id_lote_ag.in_(lote_ids))
        .group_by(LoteAgendamentoItem.id_lote_ag)
        .all()
    )
    totals_map = {r[0]: {"total": r[1], "conciliados": r[2]} for r in totals_q}
    
    data = []
    for lote in lotes:
        dic = {c.name: getattr(lote, c.name) for c in lote.__table__.columns}
        stats = totals_map.get(lote.id_lote_ag, {"total": 0, "conciliados": 0})
        dic["total_itens"] = stats["total"]
        dic["total_conciliados"] = stats["conciliados"]
        data.append(dic)
    
    return {"data": data, "total": total}

@router.post("/gerar-lote-agendamento")
def gerar_lote_agendamento(
    request: GerarLoteAgendamentoRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and request.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
    
    # Buscar agendamentos confirmados no intervalo
    agendamentos = db.query(Agendamento).filter(
        Agendamento.id_convenio == request.id_convenio,
        Agendamento.Status == "Confirmado",
        Agendamento.data >= request.data_inicio,
        Agendamento.data <= request.data_fim
    ).all()
    
    if not agendamentos:
        raise HTTPException(status_code=404, detail="Nenhum agendamento confirmado encontrado no período.")
    
    # Criar lote
    novo_lote = LoteAgendamento(
        id_convenio=request.id_convenio,
        data_inicio=request.data_inicio,
        data_fim=request.data_fim,
        status="Aberto"
    )
    db.add(novo_lote)
    db.flush()
    
    # Inserir itens em bulk
    items_to_add = [
        LoteAgendamentoItem(
            id_lote_ag=novo_lote.id_lote_ag,
            id_agendamento=ag.id_agendamento,
            status_conciliacao="Não Conciliado"
        )
        for ag in agendamentos
    ]
    db.bulk_save_objects(items_to_add)
    db.commit()
    
    return {
        "message": f"Lote de agendamento criado com {len(agendamentos)} itens.",
        "id_lote_ag": novo_lote.id_lote_ag,
        "total_itens": len(agendamentos)
    }

@router.get("/itens/{id_lote_ag}")
def list_itens_lote_agendamento(
    id_lote_ag: int,
    limit: int = 10000,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    lote = db.query(LoteAgendamento).filter(LoteAgendamento.id_lote_ag == id_lote_ag).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote de agendamento não encontrado.")
    
    # Query otimizada: buscar tudo de uma vez com JOINs
    rows = (
        db.query(LoteAgendamentoItem, Agendamento)
        .join(Agendamento, LoteAgendamentoItem.id_agendamento == Agendamento.id_agendamento)
        .filter(LoteAgendamentoItem.id_lote_ag == id_lote_ag)
        .offset(skip).limit(limit)
        .all()
    )
    total = len(rows) if len(rows) < limit else (
        db.query(func.count(LoteAgendamentoItem.id))
        .filter(LoteAgendamentoItem.id_lote_ag == id_lote_ag).scalar()
    )
    
    # Pre-fetch guias em batch para evitar N+1
    guia_numbers = list(set(ag.numero_guia for _, ag in rows if ag.numero_guia))
    guias_map = {}
    if guia_numbers:
        guias = db.query(BaseGuia).filter(BaseGuia.guia.in_(guia_numbers)).all()
        guias_map = {g.guia: g for g in guias}
    
    # Usar link persistente do lote
    linked_lote_convenio_id = lote.id_lote_convenio
    
    data = []
    for lai, ag in rows:
        guia_obj = guias_map.get(ag.numero_guia)
        status_ver = compute_status_verificacao(ag.data, guia_obj, ag.numero_guia)
        
        # Saldo simplificado (sem query por item)
        saldo = {"saldo": None, "icone": "-", "cor": "cinza", "texto": "N/A"}
        
        dic = {
            "id": lai.id,
            "id_lote_ag": lai.id_lote_ag,
            "id_agendamento": ag.id_agendamento,
            "paciente": ag.Nome_Paciente,
            "data": str(ag.data) if ag.data else None,
            "horario": str(ag.hora_inicio) if ag.hora_inicio else None,
            "cod_procedimento_fat": ag.cod_procedimento_fat,
            "numero_guia": ag.numero_guia,
            "status_conciliacao": lai.status_conciliacao,
            "id_faturamento_lote": lai.id_faturamento_lote,
            "status_verificacao": status_ver,
            "saldo_exec": saldo
        }
        data.append(dic)
    
    return {"data": data, "total": total, "linked_lote_convenio_id": linked_lote_convenio_id}

# ── Endpoints: Conciliação ──

@router.post("/conciliar")
def conciliar_lote(
    request: ConciliarRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Para cada item do lote de faturamento, tenta vincular com um agendamento
    do lote de agendamento, cruzando por CodigoBeneficiario e Guia.
    Usa campo texto 'carteirinha' como fallback quando id_carteirinha é NULL.
    """
    lote_conv = db.query(LoteConvenio).filter(LoteConvenio.id_lote == request.id_lote_convenio).first()
    if not lote_conv:
        raise HTTPException(status_code=404, detail="Lote de convênio não encontrado.")
    
    lote_ag = db.query(LoteAgendamento).filter(LoteAgendamento.id_lote_ag == request.id_lote_ag).first()
    if not lote_ag:
        raise HTTPException(status_code=404, detail="Lote de agendamento não encontrado.")
    
    # Persistir vínculo entre lote_agendamento e lote_convenio
    lote_ag.id_lote_convenio = lote_conv.id_lote
    
    # Carregar itens do lote de faturamento (não conciliados)
    fat_items = db.query(FaturamentoLote).filter(
        FaturamentoLote.id_lote == lote_conv.id_lote,
        FaturamentoLote.agendamento_id.is_(None)
    ).all()
    
    # Carregar itens do lote de agendamento (não conciliados)
    ag_items = (
        db.query(LoteAgendamentoItem, Agendamento)
        .join(Agendamento, LoteAgendamentoItem.id_agendamento == Agendamento.id_agendamento)
        .filter(
            LoteAgendamentoItem.id_lote_ag == request.id_lote_ag,
            LoteAgendamentoItem.status_conciliacao == "Não Conciliado"
        )
        .all()
    )
    
    # Pre-fetch all carteirinhas necessárias em batch (com TRIM)
    cart_texts_raw = list(set(ag.carteirinha.strip() for _, ag in ag_items if ag.carteirinha))
    cart_ids = list(set(ag.id_carteirinha for _, ag in ag_items if ag.id_carteirinha))
    
    carts_by_text = {}
    if cart_texts_raw:
        for c in db.query(Carteirinha).filter(Carteirinha.carteirinha.in_(cart_texts_raw)).all():
            carts_by_text[c.carteirinha] = c
    
    carts_by_id = {}
    if cart_ids:
        for c in db.query(Carteirinha).filter(Carteirinha.id.in_(cart_ids)).all():
            carts_by_id[c.id] = c
    
    # Indexar agendamentos por (codigo_beneficiario, numero_guia)
    ag_map = {}
    for lai, ag in ag_items:
        cod_ben = None
        if ag.id_carteirinha and ag.id_carteirinha in carts_by_id:
            cod_ben = carts_by_id[ag.id_carteirinha].codigo_beneficiario
        elif ag.carteirinha and ag.carteirinha.strip() in carts_by_text:
            cod_ben = carts_by_text[ag.carteirinha.strip()].codigo_beneficiario
        
        if cod_ben and ag.numero_guia:
            key = (cod_ben, ag.numero_guia)
            if key not in ag_map:
                ag_map[key] = []
            ag_map[key].append((lai, ag))
        elif ag.numero_guia:
            # Fallback index for missing cod_ben
            key = (None, ag.numero_guia)
            if key not in ag_map:
                ag_map[key] = []
            ag_map[key].append((lai, ag))
    
    count_conciliados = 0
    count_pendentes = 0
    
    for fat in fat_items:
        if not fat.Guia:
            continue
            
        key = (fat.CodigoBeneficiario, fat.Guia)
        candidates = ag_map.get(key, [])
        
        if not candidates:
            # Tentar fallback (agendamento com carteirinha inválida mas com a mesma Guia)
            candidates = ag_map.get((None, fat.Guia), [])
        
        if candidates:
            lai, ag = candidates.pop(0)
            
            fat.agendamento_id = ag.id_agendamento
            fat.dataRealizacao = ag.data
            fat.StatusConferencia = 67
            fat.StatusConciliacao = "Conciliado"
            
            lai.status_conciliacao = "Conciliado"
            lai.id_faturamento_lote = fat.id
            
            count_conciliados += 1
            
    # Contar pendentes do lote de agendamento para bater com o dashboard
    total_ag_pendentes = db.query(LoteAgendamentoItem).filter(
        LoteAgendamentoItem.id_lote_ag == request.id_lote_ag,
        LoteAgendamentoItem.status_conciliacao != "Conciliado"
    ).count()
    
    db.commit()
    
    return {
        "message": f"Conciliação concluída: {count_conciliados} vinculados, {total_ag_pendentes} itens pendentes no lote.",
        "conciliados": count_conciliados,
        "pendentes": total_ag_pendentes
    }

@router.post("/conciliar-manual")
def conciliar_manual(
    request: ConciliarManualRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Vinculação manual entre item de faturamento e agendamento. Só permite se apto a faturar."""
    fat = db.query(FaturamentoLote).filter(FaturamentoLote.id == request.id_faturamento_lote).first()
    if not fat:
        raise HTTPException(status_code=404, detail="Item de faturamento não encontrado.")
    
    ag = db.query(Agendamento).filter(Agendamento.id_agendamento == request.id_agendamento).first()
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    
    # Verificar se é apto a faturar
    if ag.numero_guia:
        guia_obj = db.query(BaseGuia).filter(BaseGuia.guia == ag.numero_guia).first()
        if guia_obj:
            status_ver = compute_status_verificacao(ag.data, guia_obj)
            if not status_ver["apto"]:
                raise HTTPException(status_code=400, detail=f"Agendamento não apto: {status_ver['texto']}")
    
    # Vincular
    fat.agendamento_id = ag.id_agendamento
    fat.dataRealizacao = ag.data
    fat.StatusConferencia = 67
    fat.StatusConciliacao = "Conciliado"
    
    # Atualizar item do lote de agendamento se existir
    lai = db.query(LoteAgendamentoItem).filter(
        LoteAgendamentoItem.id_agendamento == ag.id_agendamento
    ).first()
    if lai:
        lai.status_conciliacao = "Conciliado"
        lai.id_faturamento_lote = fat.id
    
    db.commit()
    
    return {"message": "Conciliação manual realizada com sucesso."}

@router.put("/editar-item/{id_faturamento_lote}")
def editar_item(
    id_faturamento_lote: int,
    request: EditarItemRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Edita campos do item de faturamento e tenta reconciliar automaticamente."""
    fat = db.query(FaturamentoLote).filter(FaturamentoLote.id == id_faturamento_lote).first()
    if not fat:
        raise HTTPException(status_code=404, detail="Item de faturamento não encontrado.")
    
    # Aplicar edições
    if request.dataRealizacao is not None:
        fat.dataRealizacao = request.dataRealizacao
    if request.Guia is not None:
        fat.Guia = request.Guia
    
    # Tentar conciliar automaticamente
    conciliado = False
    if fat.Guia and fat.CodigoBeneficiario and not fat.agendamento_id:
        # Buscar agendamentos com a mesma guia
        ags = db.query(Agendamento).filter(
            Agendamento.numero_guia == fat.Guia,
            Agendamento.Status == "Confirmado"
        ).all()
        
        ag = None
        for a in ags:
            cod_ben = resolve_codigo_beneficiario(db, a)
            if cod_ben == fat.CodigoBeneficiario or cod_ben is None:
                ag = a
                break
                
        if ag:
            guia_obj = db.query(BaseGuia).filter(BaseGuia.guia == ag.numero_guia).first()
            status_ver = compute_status_verificacao(ag.data, guia_obj, ag.numero_guia)
            if status_ver["apto"]:
                fat.agendamento_id = ag.id_agendamento
                fat.dataRealizacao = ag.data
                fat.StatusConferencia = 67
                fat.StatusConciliacao = "Conciliado"
                conciliado = True
                # Atualizar item do lote de agendamento
                lai = db.query(LoteAgendamentoItem).filter(
                    LoteAgendamentoItem.id_agendamento == ag.id_agendamento
                ).first()
                if lai:
                    lai.status_conciliacao = "Conciliado"
                    lai.id_faturamento_lote = fat.id
    
    # Revalidar status_verificacao
    status_ver = None
    if fat.agendamento_id:
        ag = db.query(Agendamento).filter(Agendamento.id_agendamento == fat.agendamento_id).first()
        if ag and ag.numero_guia:
            guia_obj = db.query(BaseGuia).filter(BaseGuia.guia == ag.numero_guia).first()
            if guia_obj:
                status_ver = compute_status_verificacao(ag.data, guia_obj)
    
    db.commit()
    
    return {
        "message": "Item atualizado.",
        "auto_conciliado": conciliado,
        "status_verificacao": status_ver
    }

@router.get("/candidatos/{id_faturamento_lote}")
def listar_candidatos(
    id_faturamento_lote: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lista agendamentos candidatos para conciliação manual baseado na guia do item."""
    fat = db.query(FaturamentoLote).filter(FaturamentoLote.id == id_faturamento_lote).first()
    if not fat:
        raise HTTPException(status_code=404, detail="Item de faturamento não encontrado.")
    
    if not fat.Guia:
        return {"data": [], "message": "Item sem guia vinculada."}
    
    # Buscar agendamentos que tenham a mesma guia
    agendamentos = db.query(Agendamento).filter(
        Agendamento.numero_guia == fat.Guia,
        Agendamento.Status == "Confirmado"
    ).all()
    
    data = []
    for ag in agendamentos:
        guia_obj = db.query(BaseGuia).filter(BaseGuia.guia == ag.numero_guia).first()
        status_ver = compute_status_verificacao(ag.data, guia_obj) if guia_obj else {
            "icone": "!", "texto": "Sem guia", "apto": False
        }
        
        data.append({
            "id_agendamento": ag.id_agendamento,
            "paciente": ag.Nome_Paciente,
            "data": str(ag.data) if ag.data else None,
            "horario": str(ag.hora_inicio) if ag.hora_inicio else None,
            "cod_procedimento_fat": ag.cod_procedimento_fat,
            "numero_guia": ag.numero_guia,
            "status_verificacao": status_ver,
            "apto": status_ver["apto"]
        })
    
    return {"data": data}

@router.get("/candidatos-fat-por-guia")
def listar_candidatos_fat_por_guia(
    numero_guia: str,
    id_lote_convenio: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lista itens de faturamento candidatos a partir de uma guia (busca do lado agendamento)."""
    query = db.query(FaturamentoLote).filter(
        FaturamentoLote.Guia == numero_guia,
        FaturamentoLote.agendamento_id.is_(None)
    )
    if id_lote_convenio:
        query = query.filter(FaturamentoLote.id_lote == id_lote_convenio)
    
    fats = query.all()
    data = []
    for fat in fats:
        data.append({
            "id": fat.id,
            "detalheId": fat.detalheId,
            "Guia": fat.Guia,
            "CodigoBeneficiario": fat.CodigoBeneficiario,
            "dataRealizacao": str(fat.dataRealizacao) if fat.dataRealizacao else None,
            "StatusConferencia": fat.StatusConferencia,
            "id_lote": fat.id_lote
        })
    return {"data": data}

class ConciliarManualPorAgendamentoRequest(BaseModel):
    id_agendamento: int
    id_faturamento_lote: int  # id (PK) da tabela faturamento_lotes

@router.post("/conciliar-manual-ag")
def conciliar_manual_por_agendamento(
    request: ConciliarManualPorAgendamentoRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Vincula um agendamento a um item de faturamento (partindo do lado agendamento)."""
    ag = db.query(Agendamento).filter(Agendamento.id_agendamento == request.id_agendamento).first()
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    
    fat = db.query(FaturamentoLote).filter(FaturamentoLote.id == request.id_faturamento_lote).first()
    if not fat:
        raise HTTPException(status_code=404, detail="Item de faturamento não encontrado.")
    
    if fat.agendamento_id:
        raise HTTPException(status_code=400, detail="Este item de faturamento já está conciliado.")
    
    fat.agendamento_id = ag.id_agendamento
    fat.dataRealizacao = ag.data
    fat.StatusConferencia = 67
    fat.StatusConciliacao = "Conciliado"
    
    lai = db.query(LoteAgendamentoItem).filter(
        LoteAgendamentoItem.id_agendamento == ag.id_agendamento
    ).first()
    if lai:
        lai.status_conciliacao = "Conciliado"
        lai.id_faturamento_lote = fat.id
    
    db.commit()
    return {"message": "Conciliação manual realizada com sucesso."}
