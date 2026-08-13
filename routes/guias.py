from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user, get_effective_user_id
from models import BaseGuia, Carteirinha, Job
from typing import Optional
from datetime import date, datetime, timedelta
from openpyxl import Workbook
import io
from timezone_utils import localize_datetime

router = APIRouter(
    prefix="/guias",
    tags=["Guias"]
)

@router.get("/")
def list_guias(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    created_at_start: Optional[date] = None, 
    created_at_end: Optional[date] = None,
    carteirinha_id: Optional[int] = None,
    id_convenio: Optional[int] = None,
    aba: Optional[str] = None,
    status: Optional[str] = None,
    senha: Optional[str] = None,
    codigo_terapia: Optional[str] = None,
    limit: int = 25,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    from sqlalchemy import func, case, and_, or_
    from models import Agendamento
    
    # Montar parâmetros da consulta para gerar chave de cache única
    cache_params = {
        "start_date": str(start_date) if start_date else None,
        "end_date": str(end_date) if end_date else None,
        "created_at_start": str(created_at_start) if created_at_start else None,
        "created_at_end": str(created_at_end) if created_at_end else None,
        "carteirinha_id": carteirinha_id,
        "id_convenio": id_convenio,
        "aba": aba,
        "status": status,
        "senha": senha,
        "codigo_terapia": codigo_terapia,
        "limit": limit,
        "skip": skip
    }
    
    from cache import cache
    cached_res = cache.get(current_user.id, "guias", cache_params)
    if cached_res:
        return cached_res

    # Auto-sincronizar guias extraídas pelo worker em background para evitar travamento
    if background_tasks:
        try:
            from services.guias_sync_service import sync_completed_worker_jobs_bg
            background_tasks.add_task(sync_completed_worker_jobs_bg)
        except Exception as e:
            print(f"Error scheduling completed jobs during list_guias: {e}")
    
    subq_query = db.query(
        Agendamento.numero_guia,
        func.sum(case((Agendamento.Status == 'Confirmado', 1), else_=0)).label('q_realizadas'),
        func.sum(case((Agendamento.Status == 'A Confirmar', 1), else_=0)).label('q_a_confirmar')
    )
    if not current_user.is_admin:
        subq_query = subq_query.filter(Agendamento.user_id == get_effective_user_id(current_user))
    subq = subq_query.group_by(Agendamento.numero_guia).subquery()

    query = db.query(
        BaseGuia,
        func.coalesce(subq.c.q_realizadas, 0).label('computed_realizadas'),
        func.coalesce(subq.c.q_a_confirmar, 0).label('computed_a_confirmar'),
        Carteirinha.paciente.label('nome_paciente'),
        Carteirinha.carteirinha.label('carteirinha_numero')
    ).select_from(BaseGuia)\
     .outerjoin(subq, BaseGuia.guia == subq.c.numero_guia)\
     .outerjoin(Carteirinha, and_(
         BaseGuia.carteirinha_id == Carteirinha.id,
         or_(
             BaseGuia.user_id == Carteirinha.user_id,
             Carteirinha.user_id.is_(None),
             BaseGuia.user_id.is_(None)
         )
     ))
    
    # Isolation: if user has a convenio, only show guias from that convenio
    from dependencies import get_allowed_convenio_ids
    allowed_ids = get_allowed_convenio_ids(current_user)
    
    if not current_user.is_admin:
        query = query.filter(BaseGuia.user_id == get_effective_user_id(current_user))
    
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
             raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        query = query.filter(BaseGuia.id_convenio == id_convenio)
    elif allowed_ids:
        query = query.filter(BaseGuia.id_convenio.in_(allowed_ids))
    
    if created_at_start:
        query = query.filter(BaseGuia.updated_at >= created_at_start)
    if created_at_end:
        # Inclusive end date (until end of day)
        end_dt = datetime.combine(created_at_end, datetime.min.time()) + timedelta(days=1)
        query = query.filter(BaseGuia.updated_at < end_dt)
    if carteirinha_id:
        query = query.filter(BaseGuia.carteirinha_id == carteirinha_id)
    if status:
        query = query.filter(BaseGuia.status_guia.ilike(f'%{status}%'))
    if senha:
        query = query.filter(BaseGuia.senha.ilike(f'%{senha}%'))
    if codigo_terapia:
        query = query.filter(BaseGuia.codigo_terapia.ilike(f'%{codigo_terapia}%'))
        
    if aba == "autorizadas":
        query = query.filter(
            or_(
                BaseGuia.status_guia.ilike('%autorizad%'),
                BaseGuia.status_guia.ilike('%liberad%')
            )
        )



    # Rotinas internas de sistema que NÃO devem aparecer no modal de Solicitações
    _ROTINAS_SISTEMA_EVOLUIR = [
        "op2_obterDetalhes", "op3_ListarPTS", "op5_ImportCorpoClinico",
        "op1_importPacientes", "op6_baixarFaturados", "op4_atualizarDataPTS",
    ]

    # Fetch active/failed jobs when looking at solicitacoes
    jobs_data = []
    if aba == "solicitacoes":
        job_query = db.query(Job, Carteirinha.paciente.label('nome_paciente'), Carteirinha.carteirinha.label('carteirinha_numero'))\
            .outerjoin(Carteirinha, Job.carteirinha_id == Carteirinha.id)
        if not current_user.is_admin:
            job_query = job_query.filter(Job.user_id == get_effective_user_id(current_user))
        if id_convenio:
            job_query = job_query.filter(Job.id_convenio == id_convenio)
        # Excluir jobs de rotinas internas de sistema
        job_query = job_query.filter(~Job.rotina.in_(_ROTINAS_SISTEMA_EVOLUIR))
        if status:
            status_lower = status.lower()
            if "pendente" in status_lower:
                job_query = job_query.filter(Job.status.in_(["pending", "processing"]))
            elif "negad" in status_lower:
                job_query = job_query.filter(Job.status == "error")
            else:
                job_query = job_query.filter(Job.id == -1)
        else:
            job_query = job_query.filter(Job.status.in_(["pending", "processing", "error"]))
        db_jobs = job_query.order_by(Job.created_at.desc()).all()
        
        for job_row in db_jobs:
            job_obj = job_row[0]
            nome_paciente = job_row[1]
            carteirinha_numero = job_row[2]
            
            params = job_obj.params or {}
            if isinstance(params, str):
                try:
                    import json
                    params = json.loads(params)
                except Exception:
                    params = {}
            elif not isinstance(params, dict):
                params = {}
                
            status_desc = job_obj.status
            if job_obj.status == "error" and job_obj.error_message:
                status_desc = f"Erro: {job_obj.error_message}"
            elif job_obj.status == "pending":
                status_desc = "Pendente (Fila)"
            elif job_obj.status == "processing":
                status_desc = "Processando"
                
            loc_created = localize_datetime(job_obj.created_at) if job_obj.created_at else None
            loc_updated = localize_datetime(job_obj.updated_at) if job_obj.updated_at else None
            jobs_data.append({
                "id": f"job-{job_obj.id}",
                "carteirinha_id": job_obj.carteirinha_id,
                "id_convenio": job_obj.id_convenio,
                "cod_prestador": params.get("codigo_prestador") or "",
                "codigo_beneficiario": params.get("carteira") or carteirinha_numero or "",
                "guia": f"Solicitação #{job_obj.id}",
                "guia_prestador": "",
                "data_solicitacao": loc_created.date() if loc_created else None,
                "data_autorizacao": None,
                "senha": "",
                "status_guia": status_desc,
                "validade": None,
                "codigo_terapia": params.get("codigoProcedimento_aut") or "",
                "nome_terapia": "Solicitação em andamento...",
                "qtde_solicitada": int(params.get("qtde") or 1),
                "sessoes_autorizadas": 0,
                "sessoes_realizadas": 0,
                "saldo": 0,
                "created_at": loc_created,
                "updated_at": loc_updated,
                "nome_paciente": nome_paciente or "Paciente",
                "carteirinha_numero": carteirinha_numero or params.get("carteira") or ""
            })

    if aba == "solicitacoes":
        from models import Solicitacao
        
        sol_query = db.query(
            Solicitacao,
            Carteirinha.paciente.label('nome_paciente'),
            Carteirinha.carteirinha.label('carteirinha_numero')
        ).select_from(Solicitacao)\
         .outerjoin(Carteirinha, and_(
             Solicitacao.carteirinha_id == Carteirinha.id,
             or_(
                 Solicitacao.user_id == Carteirinha.user_id,
                 Carteirinha.user_id.is_(None),
                 Solicitacao.user_id.is_(None)
             )
         ))
         
        if not current_user.is_admin:
            sol_query = sol_query.filter(Solicitacao.user_id == get_effective_user_id(current_user))
            
        # Excluir guias autorizadas/liberadas e solicitações que já possuem guias correspondentes na base_guias para evitar duplicação
        from sqlalchemy import exists
        
        has_base_guia_subq = exists().where(
            and_(
                BaseGuia.guia == Solicitacao.guia,
                BaseGuia.id_convenio == Solicitacao.id_convenio,
                BaseGuia.codigo_terapia == Solicitacao.codigo_terapia,
                BaseGuia.user_id == Solicitacao.user_id
            )
        )
        sol_query = sol_query.filter(
            and_(
                ~or_(
                    Solicitacao.status_solicitacao.ilike('%autorizad%'),
                    Solicitacao.status_solicitacao.ilike('%liberad%')
                ),
                Solicitacao.base_guia_id.is_(None),
                ~has_base_guia_subq
            )
        )
            
        if id_convenio:
            sol_query = sol_query.filter(Solicitacao.id_convenio == id_convenio)
        elif allowed_ids:
            sol_query = sol_query.filter(Solicitacao.id_convenio.in_(allowed_ids))
            
        if created_at_start:
            sol_query = sol_query.filter(Solicitacao.updated_at >= created_at_start)
        if created_at_end:
            end_dt = datetime.combine(created_at_end, datetime.min.time()) + timedelta(days=1)
            sol_query = sol_query.filter(Solicitacao.updated_at < end_dt)
        if carteirinha_id:
            sol_query = sol_query.filter(Solicitacao.carteirinha_id == carteirinha_id)
        if status:
            sol_query = sol_query.filter(Solicitacao.status_solicitacao.ilike(f'%{status}%'))
        if senha:
            sol_query = sol_query.filter(Solicitacao.senha.ilike(f'%{senha}%'))
        if codigo_terapia:
            sol_query = sol_query.filter(Solicitacao.codigo_terapia.ilike(f'%{codigo_terapia}%'))
            
        db_sols = sol_query.order_by(Solicitacao.created_at.desc()).all()
        
        from sqlalchemy import inspect
        sol_mapper = inspect(Solicitacao)
        solicitacoes_data = []
        for row in db_sols:
            sol_obj = row[0]
            nome_paciente = row[1]
            carteirinha_numero = row[2]
            
            s_dict = {attr.key: getattr(sol_obj, attr.key) for attr in sol_mapper.column_attrs}
            s_dict['status_guia'] = s_dict['status_solicitacao']
            s_dict['nome_paciente'] = nome_paciente
            s_dict['carteirinha_numero'] = carteirinha_numero
            s_dict['sessoes_realizadas'] = 0
            s_dict['saldo'] = s_dict.get('sessoes_autorizadas', 0)
            
            # Localize datetimes
            s_dict['created_at'] = localize_datetime(s_dict.get('created_at'))
            s_dict['updated_at'] = localize_datetime(s_dict.get('updated_at'))
            s_dict['data_solicitacao'] = s_dict['created_at'].date() if s_dict['created_at'] else None
            
            solicitacoes_data.append(s_dict)
            
        combined = jobs_data + solicitacoes_data
        combined.sort(key=lambda x: x.get("created_at") or datetime.min, reverse=True)
        total = len(combined)
        guias_data = combined[skip : skip + limit]
        
    else:
        from sqlalchemy import inspect
        base_guia_mapper = inspect(BaseGuia)
        results = query.order_by(BaseGuia.created_at.desc()).limit(limit).offset(skip).all()
        guias_data = []
        for row in results:
            guia_obj = row[0]
            q_realizadas = int(row[1] or 0)
            q_a_confirmar = int(row[2] or 0)
            nome_paciente = row[3]
            carteirinha_numero = row[4]
            
            g_dict = {attr.key: getattr(guia_obj, attr.key) for attr in base_guia_mapper.column_attrs}
            g_dict['sessoes_realizadas'] = q_realizadas
            g_dict['nome_paciente'] = nome_paciente
            g_dict['carteirinha_numero'] = carteirinha_numero
            
            # Localize datetimes
            g_dict['created_at'] = localize_datetime(g_dict.get('created_at'))
            g_dict['updated_at'] = localize_datetime(g_dict.get('updated_at'))
            g_dict['data_autorizacao'] = localize_datetime(g_dict.get('data_autorizacao'))
            g_dict['validade'] = localize_datetime(g_dict.get('validade'))
            
            auth = g_dict.get('sessoes_autorizadas') or 0
            g_dict['saldo'] = auth - (q_realizadas + q_a_confirmar)
            guias_data.append(g_dict)
            
        total = query.count()
        
    res_payload = {"data": guias_data, "total": total, "skip": skip, "limit": limit}
    cache.set(current_user.id, "guias", cache_params, res_payload, ttl=30)
    return res_payload

@router.get("/export")
def export_guias(
    created_at_start: Optional[str] = Query(None, description="Start Date (YYYY-MM-DD)"),
    created_at_end: Optional[str] = Query(None, description="End Date (YYYY-MM-DD)"),
    carteirinha_id: Optional[int] = Query(None, description="Filter by Carteirinha ID"),
    id_convenio: Optional[int] = Query(None, description="Filter by Convenio ID"),
    aba: Optional[str] = Query(None, description="Filter by ABA (autorizadas/solicitacoes)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    senha: Optional[str] = Query(None, description="Filter by senha"),
    codigo_terapia: Optional[str] = Query(None, description="Filter by codigo terapia"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Optimized Excel Generation
    try:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Guias")
        
        headers = ["Carteirinha", "Paciente", "Guia", "Senha", "Status", "Data Solicitacao", 
                   "Data Autorizacao", "Codigo", "Qtde Solicit", "Qtde Aut", "Validade", "Data Importacao"]
        ws.append(headers)
        
        # Helper to format date
        def fmt_date(d):
            return d.strftime("%d/%m/%Y") if d else ""

        
        print("DEBUG: Executing Query with raw tuples...")
        # Use yield_per to reduce memory overhead and tuple selection to avoid N+1 and lazy loading issues
        query = db.query(
            Carteirinha.carteirinha,         # 0
            Carteirinha.paciente,            # 1
            BaseGuia.guia,                   # 2
            BaseGuia.data_autorizacao,       # 3
            BaseGuia.senha,                  # 4
            BaseGuia.validade,               # 5
            BaseGuia.codigo_terapia,         # 6
            BaseGuia.qtde_solicitada,        # 7
            BaseGuia.sessoes_autorizadas,    # 8
            BaseGuia.created_at,             # 9
            BaseGuia.codigo_beneficiario,    # 10
            BaseGuia.status_guia,            # 11
            BaseGuia.data_solicitacao        # 12
        ).select_from(BaseGuia).outerjoin(Carteirinha, BaseGuia.carteirinha_id == Carteirinha.id)

        # Isolation: if user has a convenio, only show guias from that convenio
        from dependencies import get_allowed_convenio_ids
        allowed_ids = get_allowed_convenio_ids(current_user)
        
        if not current_user.is_admin:
            query = query.filter(BaseGuia.user_id == get_effective_user_id(current_user))
        
        if id_convenio:
            if allowed_ids and id_convenio not in allowed_ids:
                 raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
            query = query.filter(BaseGuia.id_convenio == id_convenio)
        elif allowed_ids:
            query = query.filter(BaseGuia.id_convenio.in_(allowed_ids))

        if created_at_start:
            query = query.filter(BaseGuia.updated_at >= created_at_start)
        if created_at_end:
            # Add one day to include full end date
            end_dt = datetime.strptime(created_at_end, '%Y-%m-%d').date() + timedelta(days=1)
            query = query.filter(BaseGuia.updated_at <= str(end_dt))
        if carteirinha_id:
            query = query.filter(BaseGuia.carteirinha_id == carteirinha_id)
        if status:
            query = query.filter(BaseGuia.status_guia.ilike(f'%{status}%'))
        if senha:
            query = query.filter(BaseGuia.senha.ilike(f'%{senha}%'))
        if codigo_terapia:
            query = query.filter(BaseGuia.codigo_terapia.ilike(f'%{codigo_terapia}%'))
            
        if aba == "autorizadas":
            query = query.filter(BaseGuia.status_guia.ilike('%autorizad%'))
        elif aba == "solicitacoes":
            query = query.filter(~BaseGuia.status_guia.ilike('%autorizad%'))
        
        results = query.yield_per(1000)
        
        count = 0
        for row in results:
            count += 1
            ws.append([
                row.carteirinha or row.codigo_beneficiario or "",
                row.paciente or "",
                row.guia,
                row.senha,
                row.status_guia,
                fmt_date(row.data_solicitacao),
                fmt_date(row.data_autorizacao),
                row.codigo_terapia,
                row.qtde_solicitada,
                row.sessoes_autorizadas,
                fmt_date(row.validade),
                row.created_at.strftime("%d/%m/%Y %H:%M:%S") if row.created_at else ""
            ])

        print(f"DEBUG: Processed {count} rows. Saving Workbook...")
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        headers = {
            'Content-Disposition': 'attachment; filename="guias_exportadas.xlsx"'
        }
        return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        print(f"Export Error: {e}")
        # Return the actual error details for debugging instead of generic 500
        # In production this might be bad, but for debugging now it's essential
        raise HTTPException(status_code=500, detail=f"Erro ao gerar arquivo: {str(e)}")

@router.get("/relatorios")
def list_relatorios(
    id_paciente: Optional[str] = None,
    tipo_relatorio: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    from models import RelatorioClinico
    query = db.query(RelatorioClinico)
    
    # Se não for admin, filtra pelo user_id logado (tenant)
    if not current_user.is_admin:
        query = query.filter(RelatorioClinico.user_id == current_user.id)
        
    if id_paciente:
        query = query.filter(RelatorioClinico.id_paciente == id_paciente)
        
    if tipo_relatorio:
        query = query.filter(RelatorioClinico.tipo_relatorio == tipo_relatorio)
        
    relatorios = query.order_by(RelatorioClinico.data.desc(), RelatorioClinico.id.desc()).all()
    return relatorios
