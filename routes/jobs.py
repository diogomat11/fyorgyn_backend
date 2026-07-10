from fastapi import APIRouter, Depends, HTTPException, Body, Query, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from dependencies import get_current_user
from sqlalchemy.orm import Session
from database import get_db
from models import Job, Carteirinha, Convenio, UserConvenio, CorpoClinico, BaseGuia, Log
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime, timedelta
import pandas as pd
import json
from io import BytesIO
import os
import shutil
import uuid
import requests
import re
import urllib.parse
from security_utils import decrypt_password

router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"]
)

UPLOAD_DIR = os.path.join("uploads", "anexos")

def get_evoluir_session(db: Session, user_id: int) -> requests.Session:
    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == user_id,
        UserConvenio.id_convenio == 100 # Evoluir
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise ValueError("Credenciais do Evoluir não encontradas para este usuário.")
        
    username = uconv.login
    password = decrypt_password(uconv.senha_criptografada)
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    login_url = "https://sistemaevoluir.com.br/login"
    try:
        r_get = session.get(login_url, timeout=10)
    except Exception as e:
        raise ConnectionError(f"Erro de timeout ou conexão ao acessar Evoluir: {str(e)}")
        
    if r_get.status_code != 200:
        raise ConnectionError(f"Erro ao acessar página de login da Evoluir: {r_get.status_code}")
        
    html = r_get.text
    token_match = re.search(r'name="_token"\s+value="([^"]+)"', html)
    if not token_match:
        token_match = re.search(r'csrf-token"\s+content="([^"]+)"', html)
        
    if not token_match:
        raise ValueError("Token CSRF não encontrado na página de login da Evoluir.")
        
    csrf_token = token_match.group(1)
    
    # Do login POST
    login_data = {
        "_token": csrf_token,
        "user": username,
        "password": password
    }
    
    try:
        r_post = session.post(login_url, data=login_data, allow_redirects=True, timeout=10)
    except Exception as e:
        raise ConnectionError(f"Erro de timeout ou conexão ao autenticar na Evoluir: {str(e)}")
        
    if r_post.status_code != 200 or "login" in r_post.url.lower():
        raise ConnectionError("Falha na autenticação do portal Evoluir via API.")
        
    return session


def download_evoluir_pdf_auth(db: Session, user_id: int, evoluir_url: str, base_url: str, session: Optional[requests.Session] = None) -> str:
    # 1. GET the PDF content
    parsed_url = urllib.parse.urlparse(evoluir_url)
    encoded_path = urllib.parse.quote(parsed_url.path)
    encoded_query = urllib.parse.quote(parsed_url.query, safe="=&")
    
    url_to_fetch = urllib.parse.urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        encoded_path,
        parsed_url.params,
        encoded_query,
        parsed_url.fragment
    ))

    # If session is not provided, create one and login
    if not session:
        session = get_evoluir_session(db, user_id)

    try:
        r_pdf = session.get(url_to_fetch, timeout=15)
    except Exception as e:
        raise ConnectionError(f"Erro de timeout ou conexão ao baixar anexo da Evoluir: {str(e)}")
        
    if r_pdf.status_code != 200:
        raise ConnectionError(f"Erro ao baixar PDF do Evoluir ({r_pdf.status_code}): {r_pdf.text[:200]}")
        
    # Save the file locally
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    # Extract original filename and add suffix based on the URL type
    original_basename = os.path.basename(parsed_url.path)
    base_name, ext = os.path.splitext(original_basename)
    if not ext:
        ext = ".pdf"
        
    if "/pdf/ii/" in evoluir_url:
        filename = f"{base_name}-ANEXOII{ext}"
    elif "/pdf/" in evoluir_url:
        filename = f"{base_name}-PTS{ext}"
    else:
        if not original_basename.lower().endswith(".pdf"):
            filename = f"{original_basename}.pdf"
        else:
            filename = original_basename
    
    # Clean spaces/bad chars
    clean_name = filename.replace(" ", "")
    clean_name = re.sub(r'[\\/*?:"<>|]', '_', clean_name)
    
    unique_filename = f"{uuid.uuid4().hex}_{clean_name}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    with open(file_path, "wb") as f:
        f.write(r_pdf.content)
        
    return f"{base_url}/uploads/anexos/{unique_filename}"


@router.post("/upload-anexo")
def upload_anexo(
    file: UploadFile = File(...),
    current_user = Depends(get_current_user)
):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    # Remove apenas espaços e caracteres inválidos do SO, mantendo acentos, parênteses e hífens originais
    clean_name = file.filename.replace(" ", "")
    import re
    clean_name = re.sub(r'[\\/*?:"<>|]', '_', clean_name)
    unique_filename = f"{uuid.uuid4().hex}_{clean_name}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    # Salva o arquivo localmente no disco
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar anexo: {str(e)}")
        
    return {"url": f"/uploads/anexos/{unique_filename}"}

class TemporaryPatientData(BaseModel):
    carteirinha: str
    paciente: str

class CreateJobRequest(BaseModel):
    type: str # 'single', 'multiple', 'all', 'temp'
    carteirinha_ids: Optional[List[int]] = None
    temp_patient: Optional[TemporaryPatientData] = None
    rotina: Optional[str] = None
    params: Optional[str] = None
    id_convenio: Optional[int] = None

@router.post("/")
def create_jobs(
    request: CreateJobRequest, 
    fastapi_req: Request,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    import json
    
    # Interceptar e baixar URLs do Evoluir
    if request.params:
        try:
            p_dict_temp = json.loads(request.params)
            
            shared_session = None
            downloaded_urls_cache = {}
            def get_or_download(url):
                nonlocal shared_session
                if url not in downloaded_urls_cache:
                    if shared_session is None:
                        try:
                            shared_session = get_evoluir_session(db, current_user.id)
                        except Exception as e:
                            print(f"Error in lazy login session creation: {e}")
                    
                    downloaded_urls_cache[url] = download_evoluir_pdf_auth(
                        db, current_user.id, url, str(fastapi_req.base_url).rstrip('/'), shared_session
                    )
                return downloaded_urls_cache[url]

            def intercept_and_download_urls(obj):
                if isinstance(obj, dict):
                    new_dict = {}
                    for k, v in obj.items():
                        if isinstance(v, str) and "sistemaevoluir.com.br" in v.lower():
                            new_dict[k] = get_or_download(v)
                        else:
                            new_dict[k] = intercept_and_download_urls(v)
                    return new_dict
                elif isinstance(obj, list):
                    return [intercept_and_download_urls(item) for item in obj]
                else:
                    return obj

            p_dict_temp = intercept_and_download_urls(p_dict_temp)
            request.params = json.dumps(p_dict_temp)
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=400, detail=f"Erro ao interceptar e baixar anexos da Evoluir: {str(e)}")

    # Enrich and normalize job parameters for authorization/job execution
    try:
        p_dict = json.loads(request.params) if request.params else {}
        
        # 1. Fetch patient and convenio details
        if request.carteirinha_ids and len(request.carteirinha_ids) > 0:
            cart = db.query(Carteirinha).filter(Carteirinha.id == request.carteirinha_ids[0]).first()
            if cart:
                p_dict["Paciente"] = p_dict.get("Paciente") or cart.paciente or ""
                p_dict["Carteira"] = p_dict.get("Carteira") or cart.carteirinha or ""
                p_dict["TarjaMagnetica"] = p_dict.get("TarjaMagnetica") or getattr(cart, "tarja_magnetica", "") or ""
                
                # Enrich with exact keys for IPASGO
                if request.id_convenio == 6 or cart.id_convenio == 6:
                    p_dict["carteira"] = p_dict.get("carteira") or cart.carteirinha or ""
                    p_dict["paciente_CID"] = p_dict.get("paciente_CID") or getattr(cart, "cid", "") or ""

                # Enrich with exact keys for Evoluir
                if request.id_convenio == 100 or cart.id_convenio == 100:
                    p_dict["id_paciente"] = p_dict.get("id_paciente") or getattr(cart, "id_paciente", "") or ""
                    p_dict["nome_paciente"] = p_dict.get("nome_paciente") or cart.paciente or ""
                    p_dict["paciente"] = p_dict.get("paciente") or cart.paciente or ""

                conv = db.query(Convenio).filter(Convenio.id_convenio == cart.id_convenio).first()
                if conv:
                    p_dict["convenio"] = p_dict.get("convenio") or conv.nome or ""
        
        # 2. Extract Cod_procedimento_Aut and Qtde
        procs = p_dict.get("procedimentos", [])
        if procs and len(procs) > 0:
            p_dict["Cod_procedimento_Aut"] = p_dict.get("Cod_procedimento_Aut") or procs[0].get("codigo_procedimento") or ""
            p_dict["Qtde"] = p_dict.get("Qtde") or procs[0].get("qtde_solicitada") or 1
            if request.id_convenio == 6:
                p_dict["codigoProcedimento_aut"] = p_dict.get("codigoProcedimento_aut") or procs[0].get("codigo_procedimento") or ""
                p_dict["qtde"] = p_dict.get("qtde") or str(procs[0].get("qtde_solicitada") or 1)
        elif p_dict.get("codigo_procedimento"):
            p_dict["Cod_procedimento_Aut"] = p_dict.get("Cod_procedimento_Aut") or p_dict.get("codigo_procedimento")
            p_dict["Qtde"] = p_dict.get("Qtde") or p_dict.get("qtde_solicitada") or 1
            if request.id_convenio == 6:
                p_dict["codigoProcedimento_aut"] = p_dict.get("codigoProcedimento_aut") or p_dict.get("codigo_procedimento") or ""
                p_dict["qtde"] = p_dict.get("qtde") or str(p_dict.get("qtde_solicitada") or 1)
            
        # 3. Retrieve professional details from database if id_profissional is provided
        id_prof = p_dict.get("id_profissional")
        if id_prof:
            prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == str(id_prof)).first()
            if prof:
                p_dict["Profissional_nome"] = p_dict.get("Profissional_nome") or prof.nome or ""
                p_dict["Profissional_cod_convenio"] = p_dict.get("Profissional_cod_convenio") or prof.codigo_ipasgo or ""
                p_dict["Profissional_nomeConselho"] = p_dict.get("Profissional_nomeConselho") or prof.conselho or ""
                p_dict["Profisisonal_NumerConselho"] = p_dict.get("Profisisonal_NumerConselho") or prof.registro or ""
                p_dict["Profissional_UFConselho"] = p_dict.get("Profissional_UFConselho") or prof.UF or ""
                p_dict["Profissional_CBO"] = p_dict.get("Profissional_CBO") or prof.CBO or ""
                
                if request.id_convenio == 6:
                    p_dict["profissional_codigo_ipasgo"] = p_dict.get("profissional_codigo_ipasgo") or prof.codigo_ipasgo or ""
                    p_dict["profissional_CBO"] = p_dict.get("profissional_CBO") or prof.CBO or ""
                
        # 4. Retrieve doctor (medico) details from database if id_medico is provided
        id_med = p_dict.get("id_medico")
        if id_med:
            med = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == str(id_med)).first()
            if med:
                p_dict["Medico_Nome"] = p_dict.get("Medico_Nome") or med.nome or ""
                p_dict["Medico_NomeConselho"] = p_dict.get("Medico_NomeConselho") or med.conselho or ""
                p_dict["Medico_NumeroConselho"] = p_dict.get("Medico_NumeroConselho") or med.registro or ""
                p_dict["Medico_UFConselho"] = p_dict.get("Medico_UFConselho") or med.UF or ""
                p_dict["Medico_CBO"] = p_dict.get("Medico_CBO") or med.CBO or ""
        elif p_dict.get("medico_mesmo_profissional") and id_prof:
            prof = db.query(CorpoClinico).filter(CorpoClinico.id_profissional == str(id_prof)).first()
            if prof:
                p_dict["Medico_Nome"] = p_dict.get("Medico_Nome") or prof.nome or ""
                p_dict["Medico_NomeConselho"] = p_dict.get("Medico_NomeConselho") or prof.conselho or ""
                p_dict["Medico_NumeroConselho"] = p_dict.get("Medico_NumeroConselho") or prof.registro or ""
                p_dict["Medico_UFConselho"] = p_dict.get("Medico_UFConselho") or prof.UF or ""
                p_dict["Medico_CBO"] = p_dict.get("Medico_CBO") or prof.CBO or ""

        # 5. Flatten attachments (Anexo1, TipoAnexo1, Anexo2, TipoAnexo2 ...)
        anex_list = p_dict.get("anexos", [])
        if anex_list:
            for idx, a in enumerate(anex_list):
                p_dict[f"Anexo{idx+1}"] = p_dict.get(f"Anexo{idx+1}") or a.get("nome") or ""
                p_dict[f"TipoAnexo{idx+1}"] = p_dict.get(f"TipoAnexo{idx+1}") or a.get("tipo") or ""
            
            # Map strict attachments for IPASGO
            if request.id_convenio == 6:
                TIPO_MAP = {
                    "pedido médico":          "anexo_RM",
                    "pedido medico":          "anexo_RM",
                    "relatório médico":       "anexo_RM",
                    "relatorio medico":       "anexo_RM",
                    "rm":                     "anexo_RM",
                    "avaliação inicial":      "anexo_AI",
                    "avaliacao inicial":      "anexo_AI",
                    "pts/relatório clínico":  "anexo_RC",
                    "pts/relatorio clinico":  "anexo_RC",
                    "relatório clínico":      "anexo_RC",
                    "relatorio clinico":      "anexo_RC",
                    "rc":                     "anexo_RC",
                }
                for a in anex_list:
                    tipo_key = (a.get("tipo") or "").lower().strip()
                    campo = TIPO_MAP.get(tipo_key)
                    if campo and not p_dict.get(campo):
                        p_dict[campo] = a.get("nome") or a.get("caminho") or ""

        # Exact keys for IPASGO extra parameters
        if request.id_convenio == 6:
            from datetime import date as _date
            p_dict["texto_Justificativa"] = p_dict.get("texto_Justificativa") or p_dict.get("observacao") or ""
            if not p_dict.get("dataSolicitacao"):
                p_dict["dataSolicitacao"] = _date.today().strftime("%d/%m/%Y")


        # 6. Fetch user credentials for the convenio and inject into params (makes job self-contained)
        target_conv_id = request.id_convenio
        if not target_conv_id and request.carteirinha_ids and len(request.carteirinha_ids) > 0:
            cart = db.query(Carteirinha).filter(Carteirinha.id == request.carteirinha_ids[0]).first()
            if cart:
                target_conv_id = cart.id_convenio
        
        if target_conv_id:
            uconv = db.query(UserConvenio).filter(
                UserConvenio.user_id == current_user.id,
                UserConvenio.id_convenio == target_conv_id
            ).first()
            if uconv:
                p_dict["login"] = p_dict.get("login") or uconv.login
                p_dict["senha_criptografada"] = p_dict.get("senha_criptografada") or uconv.senha_criptografada
                p_dict["cod_prestador"] = p_dict.get("cod_prestador") or uconv.cod_prestador
                p_dict["login_fat"] = p_dict.get("login_fat") or uconv.login_fat
                p_dict["senha_fat_criptografada"] = p_dict.get("senha_fat_criptografada") or uconv.senha_fat_criptografada

        # 7. Set strict_session_affinity (default True for Bradesco OP1 to avoid login conflicts)
        is_bradesco_op1 = False
        if target_conv_id == 1:
            # Rotina 1 (consulta/faturamento) ou rotinas de consulta
            if request.rotina in ['1', 'op1_consulta', 'op1_fature', 'op0_login']:
                is_bradesco_op1 = True
        
        p_dict["strict_session_affinity"] = p_dict.get("strict_session_affinity", is_bradesco_op1)
        
        request.params = json.dumps(p_dict)
    except Exception as e:
        print(f"Error parsing/augmenting job params: {e}")
    
    # Validação para OP11 do IPASGO (requer ao menos 1 parâmetro: datas, guia ou carteirinha)
    if request.rotina in ['11', 'op11_import_guias_api']:
        has_params = False
        if request.carteirinha_ids and len(request.carteirinha_ids) > 0:
            has_params = True
        if request.params:
            try:
                p_dict = json.loads(request.params)
                if (
                    p_dict.get("data_ini") or 
                    p_dict.get("data_fim") or 
                    p_dict.get("start_date") or 
                    p_dict.get("end_date") or 
                    p_dict.get("guia") or 
                    p_dict.get("numero_guia") or 
                    p_dict.get("carteira") or 
                    p_dict.get("codigoBeneficiario")
                ):
                    has_params = True
            except Exception:
                pass
        if not has_params:
            raise HTTPException(
                status_code=400,
                detail="Para criar o job da OP11, informe ao menos um parâmetro: intervalo de datas, guia ou carteirinha."
            )

    if request.rotina and "fature" in request.rotina:
        request.rotina = request.rotina.replace("_fature", "").replace("fature_", "")
        try:
            p_dict = json.loads(request.params) if request.params else {}
        except Exception:
            p_dict = {}
        p_dict["contexto"] = "fature"
        request.params = json.dumps(p_dict)

    created_count = 0
    from services import job_service
    
    from dependencies import get_allowed_convenio_ids
    allowed_ids = get_allowed_convenio_ids(current_user)
    
    if request.id_convenio:
        if allowed_ids and request.id_convenio not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        target_convenio = request.id_convenio
    else:
        target_convenio = allowed_ids[0] if allowed_ids else None
    
    if request.type == 'all':
        created_count = job_service.create_all_jobs(db, id_convenio=target_convenio, rotina=request.rotina, params=request.params, user_id=current_user.id)
            
    elif request.type in ['single', 'multiple']:
        is_standalone = (target_convenio == 6 and request.rotina in [
            '3', 'op3_import_guias', 
            '6', 'op6_check_baixados', 
            '7', 'op7_fat_facplan', 
            '11', 'op11_import_guias_api', 
            '12', 'op12_impressao_api',
            '13', 'op13_criar_lote',
            '14', 'op14_cancelar_lote'
        ]) or (target_convenio == 100 and request.rotina in [
            '1', 'op1', 'op1_importPacientes', 'op5_ImportCorpoClinico',
            'op6_baixarFaturados', 'op4_atualizarDataPTS'
        ])
        
        if not request.carteirinha_ids:
            if is_standalone and request.type == 'single':
                # Forward standalone job to backend_worker
                p_dict = json.loads(request.params) if request.params else {}
                p_dict["webhook_url"] = os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")
                
                # Enrich with user credentials if convenio is set
                if target_convenio and current_user.id:
                    uconv = db.query(UserConvenio).filter(
                        UserConvenio.user_id == current_user.id,
                        UserConvenio.id_convenio == target_convenio
                    ).first()
                    if uconv:
                        p_dict["login"] = p_dict.get("login") or uconv.login
                        p_dict["senha_criptografada"] = p_dict.get("senha_criptografada") or uconv.senha_criptografada
                        p_dict["cod_prestador"] = p_dict.get("cod_prestador") or uconv.cod_prestador
                        p_dict["login_fat"] = p_dict.get("login_fat") or uconv.login_fat
                        p_dict["senha_fat_criptografada"] = p_dict.get("senha_fat_criptografada") or uconv.senha_fat_criptografada
                
                job_payload = {
                    "carteirinha_id": None,
                    "id_convenio": target_convenio,
                    "rotina": request.rotina,
                    "priority": 0,
                    "params": p_dict,
                    "max_attempts": 3
                }
                
                from services.job_service import _send_jobs_to_worker
                _send_jobs_to_worker([job_payload])
                created_count = 1
            else:
                raise HTTPException(status_code=400, detail="carteirinha_ids required for single/multiple")
        else:
            # Se não for admin, verificar posse das carteirinhas
            if not current_user.is_admin:
                count_carteirinhas = db.query(Carteirinha).filter(
                    Carteirinha.id.in_(request.carteirinha_ids),
                    Carteirinha.user_id == current_user.id
                ).count()
                if count_carteirinhas != len(request.carteirinha_ids):
                    raise HTTPException(status_code=403, detail="Uma ou mais carteirinhas não pertencem ao seu usuário.")

            # Special validation for IPASGO printing jobs (routine 5 or 12)
            if target_convenio == 6 and request.rotina in ['5', 'op5_impress_guia', '12', 'op12_impressao_api']:
                import json
                try:
                    p = json.loads(request.params or '{}')
                    guia_num = p.get("numero_guia")
                    if guia_num:
                        # Check if this guide belongs to the user and is authorized
                        query_guia = db.query(BaseGuia).filter(
                            BaseGuia.guia == guia_num,
                            BaseGuia.status_guia.ilike('%autorizad%')
                        )
                        if not current_user.is_admin:
                            query_guia = query_guia.filter(BaseGuia.user_id == current_user.id)
                        
                        if request.carteirinha_ids:
                            query_guia = query_guia.filter(BaseGuia.carteirinha_id.in_(request.carteirinha_ids))
                            
                        valid_guia = query_guia.first()
                        if not valid_guia:
                            raise HTTPException(status_code=400, detail="Apenas guias autorizadas podem ser enviadas para impressão.")
                except json.JSONDecodeError:
                    pass
            
            created_count = job_service.create_jobs_bulk(db, request.carteirinha_ids, id_convenio=target_convenio, rotina=request.rotina, params=request.params, user_id=current_user.id)
    
    elif request.type == 'temp':
        if not request.temp_patient:
             raise HTTPException(status_code=400, detail="temp_patient data required for temp job")
             
        created_count = job_service.create_temp_job(db, request.temp_patient.carteirinha, request.temp_patient.paciente, id_convenio=target_convenio, rotina=request.rotina, params=request.params, user_id=current_user.id)
                
    else:
        raise HTTPException(status_code=400, detail="Invalid job type")

    db.commit()
    
    # Se a rotina for op1_autorizar_facplan, vamos criar registros na tabela solicitacoes
    if request.rotina == 'op1_autorizar_facplan' and request.params:
        try:
            import json
            p_dict = json.loads(request.params)
            
            # Buscar os jobs correspondentes criados recentemente
            recent_jobs = db.query(Job).filter(
                Job.user_id == current_user.id,
                Job.rotina == 'op1_autorizar_facplan',
                Job.status == 'pending'
            ).order_by(Job.id.desc()).limit(created_count).all()
            
            from models import Solicitacao
            for job_row_obj in recent_jobs:
                existing_sol = db.query(Solicitacao).filter(Solicitacao.job_id == job_row_obj.id).first()
                if existing_sol:
                    continue
                    
                sol = Solicitacao(
                    user_id=current_user.id,
                    carteirinha_id=job_row_obj.carteirinha_id,
                    id_convenio=job_row_obj.id_convenio,
                    guia=f"Solicitação #{job_row_obj.id}",
                    codigo_terapia=p_dict.get("codigoProcedimento_aut") or p_dict.get("codigo_procedimento") or "",
                    nome_terapia=p_dict.get("nome_terapia") or "Aguardando autorização...",
                    qtde_solicitada=int(p_dict.get("qtde") or p_dict.get("qtde_solicitada") or 1),
                    sessoes_autorizadas=0,
                    status_solicitacao="Pendente",
                    id_profissional=p_dict.get("id_profissional"),
                    id_medico=p_dict.get("id_medico"),
                    observacao=p_dict.get("texto_Justificativa") or p_dict.get("observacao"),
                    paciente_CID=p_dict.get("paciente_CID"),
                    anexo_RM=p_dict.get("anexo_RM"),
                    anexo_AI=p_dict.get("anexo_AI"),
                    anexo_RC=p_dict.get("anexo_RC"),
                    job_id=job_row_obj.id
                )
                db.add(sol)
            db.commit()
        except Exception as e_sol:
            print(f"Error creating Solicitacao records: {e_sol}")

    try:
        from cache import cache
        cache.invalidate_tenant(current_user.id)
    except Exception as e:
        print(f"Error invalidating cache in create_jobs: {e}")
    return {"message": f"Created/Queued jobs", "count": created_count}

@router.post("/import/fature-batch")
async def import_fature_batch(
    file: UploadFile = File(...),
    id_convenio: int = Form(...),
    dataInicio: str = Form(None),
    dataFim: str = Form(None),
    regAns: str = Form(None),
    login: str = Form(None),
    password: str = Form(None),
    cod_prestador: str = Form(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    from dependencies import get_allowed_convenio_ids
    import json
    
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")

    content = await file.read()
    try:
        df = pd.read_excel(BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler arquivo Excel: {str(e)}")

    created_count = 0
    
    # Identificar colunas possíveis independentemente do case
    col_guia = next((c for c in df.columns if str(c).strip().lower() in ['guia', 'guias']), None)
    col_pac = next((c for c in df.columns if str(c).strip().lower() in ['paciente', 'nome']), None)
    
    if not col_guia:
        raise HTTPException(status_code=400, detail="Coluna 'Guia' (ou 'Guias') não encontrada na planilha.")

    encrypted_password = None
    if password:
        from security_utils import encrypt_password
        try:
            encrypted_password = encrypt_password(password)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro ao criptografar senha: {str(e)}")
            
    if not cod_prestador and login:
        uconv = db.query(UserConvenio).filter(
            UserConvenio.id_convenio == id_convenio,
            UserConvenio.login == login
        ).first()
        if uconv:
            cod_prestador = uconv.cod_prestador
        if not cod_prestador:
            uconv = db.query(UserConvenio).filter(UserConvenio.id_convenio == id_convenio).first()
            if uconv:
                cod_prestador = uconv.cod_prestador

    jobs_payload = []
    for index, row in df.iterrows():
        guia_val = str(row[col_guia]).strip()
        if pd.isna(row[col_guia]) or guia_val == 'nan' or not guia_val:
            continue
            
        paciente_val = str(row[col_pac]).strip() if col_pac else ""
        if pd.isna(row[col_pac]) or paciente_val == 'nan': paciente_val = ""

        params = {
            "guia": guia_val,
            "paciente": paciente_val,
            "contexto": "fature",
            "webhook_url": os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")
        }
        if dataInicio: params["dataInicio"] = dataInicio
        if dataFim: params["dataFim"] = dataFim
        if regAns: params["regAns"] = regAns
        
        if login:
            params["login"] = login
        if encrypted_password:
            params["senha_criptografada"] = encrypted_password
        if cod_prestador:
            params["cod_prestador"] = cod_prestador
            params["prestador_id"] = cod_prestador
        
        job_data = {
            "carteirinha_id": None,
            "id_convenio": id_convenio,
            "rotina": '1',
            "priority": 0,
            "params": params,
            "max_attempts": 3
        }
        jobs_payload.append(job_data)
        
    if jobs_payload:
        from services.job_service import _send_jobs_to_worker
        _send_jobs_to_worker(jobs_payload)
        created_count = len(jobs_payload)
        
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(current_user.id)
    except Exception as e:
        print(f"Error invalidating cache in import_fature_batch: {e}")
    return {"message": "Lote importado com sucesso", "count": created_count}

@router.get("/export/fature")
def export_fature_jobs(
    id_convenio: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    from dependencies import get_allowed_convenio_ids
    import json
    
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão.")

    query = db.query(Job).filter(Job.id_convenio == id_convenio)
    if not current_user.is_admin:
        query = query.filter(Job.user_id == current_user.id)
    jobs = query.order_by(Job.created_at.desc()).all()
    
    data = []
    for j in jobs:
        params_dict = {}
        try:
            params_dict = json.loads(j.params or '{}')
        except:
            pass
        
        guia = params_dict.get('guia') or params_dict.get('numero_guia') or ''
        paciente = params_dict.get('paciente', '')
        
        status_guia_api = ""
        if j.status == 'success':
            log_entry = db.query(Log).filter(
                Log.job_id == j.id,
                Log.level == "INFO",
                Log.message.like("Worker JSON Response:%")
            ).order_by(Log.created_at.desc()).first()
            
            if log_entry:
                try:
                    msg = log_entry.message.replace("Worker JSON Response:", "").strip()
                    resp_data = json.loads(msg)
                    results = resp_data.get("data", [])
                    if results and isinstance(results, list):
                        item = results[0]
                        desc = str(item.get("descricao") or "")
                        sg = str(item.get("status_guia") or "")
                        desc_lower = desc.lower()
                        sg_lower = sg.lower()
                        
                        if any(x in desc_lower or x in sg_lower for x in ["não", "nao", "no"]):
                            status_guia_api = "Não Localizada"
                        elif desc:
                            status_guia_api = desc
                        elif sg:
                            status_guia_api = sg
                        else:
                            status_guia_api = "Sucesso"
                    else:
                        status_guia_api = "Sucesso"
                except Exception:
                    status_guia_api = "Sucesso"
            else:
                status_guia_api = "Sucesso"
        elif j.status == 'error':
            status_guia_api = "Erro"
        elif j.status == 'pending':
            status_guia_api = "Pendente"
        elif j.status == 'processing':
            status_guia_api = "Processando"
        else:
            status_guia_api = j.status
            
        data.append({
            "Job ID": j.id,
            "Data Criação": j.created_at.strftime("%d/%m/%Y %H:%M:%S") if j.created_at else "",
            "Guia": guia,
            "Paciente": paciente,
            "Rotina": j.rotina,
            "Status Job": status_guia_api,
            "Tentativas": j.attempts
        })
        
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Jobs Exportados')
    output.seek(0)
    
    headers = {
        'Content-Disposition': f'attachment; filename="jobs_fature_{id_convenio}.xlsx"',
        'Access-Control-Expose-Headers': 'Content-Disposition'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@router.get("/")
def list_jobs(
    status: Optional[str] = None,
    created_at_start: Optional[date] = None,
    created_at_end: Optional[date] = None,
    id_convenio: Optional[int] = None,
    limit: int = 25, 
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    cache_params = {
        "status": status,
        "created_at_start": str(created_at_start) if created_at_start else None,
        "created_at_end": str(created_at_end) if created_at_end else None,
        "id_convenio": id_convenio,
        "limit": limit,
        "skip": skip
    }
    
    from cache import cache
    cached_res = cache.get(current_user.id, "jobs", cache_params)
    if cached_res:
        return cached_res

    # Auto-sincronizar guias extraídas pelo worker em background para evitar travamento
    if background_tasks:
        try:
            from services.guias_sync_service import sync_completed_worker_jobs_bg
            background_tasks.add_task(sync_completed_worker_jobs_bg)
        except Exception as e:
            print(f"Error scheduling completed jobs during list_jobs: {e}")

    query = db.query(Job)
    if not current_user.is_admin:
        query = query.filter(Job.user_id == current_user.id)
    
    from dependencies import get_allowed_convenio_ids
    allowed_ids = get_allowed_convenio_ids(current_user)
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
             raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        query = query.filter(Job.id_convenio == id_convenio)
    elif allowed_ids:
        query = query.filter(Job.id_convenio.in_(allowed_ids))
    
    if status:
        query = query.filter(Job.status == status)
        
    if created_at_start:
        query = query.filter(Job.created_at >= created_at_start)
    if created_at_end:
        end_dt = datetime.combine(created_at_end, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Job.created_at < end_dt)
    
    # Order by priority desc, created_at asc
    total = query.count()
    jobs = query.order_by(Job.priority.desc(), Job.created_at.desc()).limit(limit).offset(skip).all()
    # Note: Changed order to desc created_at to show newest first
    
    results = []
    
    # Batch query error logs to avoid N+1 queries
    error_job_ids = [j.id for j in jobs if j.status == 'error']
    error_logs_map = {}
    if error_job_ids:
        errs = db.query(Log).filter(Log.job_id.in_(error_job_ids), Log.level == "ERROR").all()
        for log in sorted(errs, key=lambda x: x.created_at):
            error_logs_map[log.job_id] = log.message

    for j in jobs:
        j_dict = {
            "id": j.id,
            "carteirinha_id": j.carteirinha_id,
            "id_convenio": j.id_convenio,
            "rotina": j.rotina,
            "params": j.params,
            "status": j.status,
            "attempts": j.attempts,
            "priority": j.priority,
            "locked_by": j.locked_by,
            "timeout": j.timeout,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
            "error_message": None
        }
        if j.status == 'error':
            last_err_msg = error_logs_map.get(j.id)
            if last_err_msg:
                msg_lower = last_err_msg.lower()
                if "carteira inv" in msg_lower or "dígito" in msg_lower or "invalida" in msg_lower:
                    j_dict["error_message"] = "Carteira inválida"
                else:
                    j_dict["error_message"] = last_err_msg
        results.append(j_dict)
    
    res_payload = {"data": results, "total": total, "skip": skip, "limit": limit}
    cache.set(current_user.id, "jobs", cache_params, res_payload, ttl=15)
    return res_payload

@router.delete("/{id}")
def delete_job(id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    job = db.query(Job).filter(Job.id == id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    if not current_user.is_admin and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissao para este job.")
        
    # Validation: Only delete if error and attempts > 3
    # User said: "probido exclusao de jobs em andamento ou com status sucess"
    # "um Job so podera ser excluido se status seja error e tentativas maior que 3"
    
    allowed = (job.status == 'error' and (job.attempts or 0) > 3)
    # Or maybe allow pending if it's stuck? User didn't specify. Sticking to strict rule.
    
    if not allowed:
         raise HTTPException(status_code=400, detail="Exclusao permitida apenas para Jobs com erro e mais de 3 tentativas.")
         
    db.delete(job)
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(current_user.id)
    except Exception as e:
        print(f"Error invalidating cache in delete_job: {e}")
    return {"message": "Job deleted"}

@router.post("/{id}/retry")
def retry_job(id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    job = db.query(Job).filter(Job.id == id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    if not current_user.is_admin and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissao para este job.")

    # Validation: Same as delete?
    # "ao clicar em reenviar exibir mensagem de confirmação, o status será alterado para pending"
    # User implied logic for buttons "Jobs error... e habilita botões de ação"
    # So implies retry is available for error jobs. 
    # And "reenviar(caso estatus seja error e tentativas maior que 3)"
    
    allowed = (job.status == 'error')
    
    if not allowed:
        raise HTTPException(status_code=400, detail="Reenvio permitido apenas para Jobs com erro.")

    job.status = 'pending'
    job.attempts = 0
    job.locked_by = None
    job.updated_at = datetime.utcnow()
    
    db.commit()
    try:
        from cache import cache
        cache.invalidate_tenant(current_user.id)
    except Exception as e:
        print(f"Error invalidating cache in retry_job: {e}")
    return {"message": "Job queued for retry", "status": job.status}


@router.post("/sync-results")
def sync_results(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """
    Sincroniza manualmente guias extraídas do worker.
    """
    try:
        from services.guias_sync_service import sync_completed_worker_jobs
        counts = sync_completed_worker_jobs(db)
        return {
            "status": "success",
            "message": "Sincronização concluída com sucesso.",
            "details": counts
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR if 'status' in globals() else 500,
            detail=f"Erro ao sincronizar resultados do worker: {str(e)}"
        )

@router.post("/{job_id}/result")
def submit_job_result(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    """
    Webhook call from worker/dispatcher to submit job results.
    """
    # 1. Fetch the job
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    # 2. Update job status and result_data
    job.status = "success"
    job.result_data = payload
    job.result_consumed = False
    job.updated_at = datetime.utcnow()
    db.commit()
    
    # 3. Immediately trigger synchronization in background
    def run_sync_in_bg():
        from database import SessionLocal
        bg_db = SessionLocal()
        try:
            from services.guias_sync_service import sync_completed_worker_jobs
            sync_completed_worker_jobs(bg_db)
        except Exception as sync_err:
            try:
                bg_db.rollback()
            except:
                pass
            print(f"Error executing immediate sync for job {job_id} in background: {sync_err}")
        finally:
            bg_db.close()

    background_tasks.add_task(run_sync_in_bg)
        
    return {"status": "success", "message": "Result received and sync queued in background"}


class WebhookPayload(BaseModel):
    job_id: int
    status: str
    result_data: Optional[dict] = None
    error_message: Optional[str] = None
    attempts: int
    rotina: str
    id_convenio: Optional[int] = None
    params: Optional[dict] = None

@router.post("/webhook")
def receive_worker_webhook(payload: WebhookPayload, db: Session = Depends(get_db)):
    """
    Recebe os resultados de jobs concluídos/falhos do backend_worker.
    Executa o parsing e a gravação de dados local no banco de dados (public).
    """
    print(f"Recebido webhook para o Job {payload.job_id} ({payload.status})")
    
    # 1. Fetch the local job record
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    
    if not job:
        # Se o job ainda não existir localmente no local_db (por atraso de replicação ou por ser outra base),
        # nós criamos um registro local para manter logs ou associar guias
        job = Job(
            id=payload.job_id,
            status=payload.status,
            id_convenio=payload.id_convenio,
            rotina=payload.rotina,
            params=payload.params,
            result_data=payload.result_data,
            error_message=payload.error_message,
            attempts=payload.attempts,
            result_consumed=False
        )
        db.add(job)
    else:
        # Atualiza dados no registro existente
        job.status = payload.status
        job.result_data = payload.result_data
        job.error_message = payload.error_message
        job.attempts = payload.attempts
        job.result_consumed = False
    db.commit()
    
    # 2. Trigger parsing e sync síncrono imediatamente para as tabelas locais (public)
    if payload.status == "success":
        from services.guias_sync_service import sync_completed_worker_jobs
        try:
            sync_completed_worker_jobs(db)
        except Exception as e:
            print(f"Erro ao executar guias_sync_service via webhook: {e}")
            
    return {"status": "success", "message": f"Webhook processado para o Job {payload.job_id}"}
