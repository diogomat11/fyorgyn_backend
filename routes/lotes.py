import json
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import LoteConvenio, FaturamentoLote, Job, Carteirinha, LoteAgendamentoItem, BaseGuia
from dependencies import get_current_user, get_allowed_convenio_ids
from typing import Optional, List
from pydantic import BaseModel
from datetime import date

router = APIRouter(
    prefix="/lotes",
    tags=["lotes"]
)

class CreateLoteRequest(BaseModel):
    id_convenio: int
    cod_prestador: str
    data_fim: date
    data_inicio: Optional[date] = None  # strategies de período (Unimed); mensal_fixo deriva sozinho

class CancelLoteRequest(BaseModel):
    cod_prestador: str

@router.get("/")
def list_lotes(
    id_convenio: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    allowed_ids = get_allowed_convenio_ids(current_user)
    
    query = db.query(LoteConvenio)
    if not current_user.is_admin:
        query = query.filter(LoteConvenio.user_id == current_user.id)
    
    if id_convenio:
        if allowed_ids and id_convenio not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")
        query = query.filter(LoteConvenio.id_convenio == id_convenio)
    elif allowed_ids:
        query = query.filter(LoteConvenio.id_convenio.in_(allowed_ids))
        
    if status:
        query = query.filter(LoteConvenio.status == status)
        
    total = query.count()
    lotes = query.order_by(LoteConvenio.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"data": lotes, "total": total}

@router.post("/")
def create_lote(
    request: CreateLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and request.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")

    # Strategy de lote por convênio (plano lote_strategy): número (IPASGO,
    # comportamento atual) × período (Unimed Goiânia/Intercâmbio — OP4 filtra
    # exatamente o período do lote e vincula todos os itens do filtro).
    from services.lote_strategy import get_lote_strategy
    strategy = get_lote_strategy(db, request.id_convenio)
    return strategy.criar_lote(db, current_user, request)


@router.get("/form-config")
def lote_form_config(
    id_convenio: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Configuração do formulário de criação de lote para o convênio
    (tipo de formato + auto-período quando mensal_fixo)."""
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão para este convênio.")

    from models import Convenio
    convenio = db.query(Convenio).filter(Convenio.id_convenio == id_convenio).first()
    if not convenio:
        raise HTTPException(status_code=404, detail="Convênio não encontrado.")

    from services.lote_strategy import get_lote_strategy
    strategy = get_lote_strategy(db, id_convenio)
    return strategy.form_config(db, convenio)

@router.post("/{id_lote}/cancelar")
def cancelar_lote(
    id_lote: int,
    request: CancelLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and lote.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão.")

    # Strategy de lote: períodos (Unimed, sem numero_lote) cancelam LOCALMENTE
    # (reversão de itens/conciliações, sem job no portal); números (IPASGO)
    # mantêm o fluxo OP14 via worker.
    from services.lote_strategy import get_lote_strategy
    strategy = get_lote_strategy(db, lote.id_convenio)
    cancelamento_local = (type(strategy).__name__ == "UnimedPeriodoLoteStrategy")

    if not cancelamento_local and not lote.numero_lote:
        raise HTTPException(status_code=400, detail="Lote ainda não possui numero_lote gerado (ainda processando?)")

    lote.status = "Cancelado" if cancelamento_local else "Cancelando"

    # Block operations on dependent items + reverse conciliation
    items = db.query(FaturamentoLote).filter(FaturamentoLote.id_lote == lote.id_lote).all()
    for item in items:
        # Se tinha agendamento vinculado, desfazer conciliação
        if item.agendamento_id:
            # Buscar o item do lote de agendamento vinculado e resetar
            lote_ag_items = db.query(LoteAgendamentoItem).filter(
                LoteAgendamentoItem.id_faturamento_lote == item.id
            ).all()
            for lai in lote_ag_items:
                lai.status_conciliacao = "Não Conciliado"
                lai.id_faturamento_lote = None
            item.agendamento_id = None
        item.StatusConciliacao = "bloqueado"

    if cancelamento_local:
        db.commit()
        return {"message": "Lote cancelado localmente. Itens bloqueados e conciliações revertidas."}

    import json
    new_job = Job(
        id_convenio=lote.id_convenio,
        rotina="14", # OP14_cancelar_lote
        status="pending",
        params=json.dumps({
            "cod_prestador": request.cod_prestador,
            "numero_lote": lote.numero_lote,
            "id_lote_interno": lote.id_lote
        }),
        user_id=current_user.id
    )
    db.add(new_job)
    db.commit()

    return {"message": "Cancelamento solicitado. Itens bloqueados."}

@router.get("/{id_lote}/faturamentos")
def list_faturamentos_por_lote(
    id_lote: int,
    limit: int = 100,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
        
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")
        
    allowed_ids = get_allowed_convenio_ids(current_user)
    if allowed_ids and lote.id_convenio not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem permissão.")
        
    if not lote.id_lote:
        return {"data": [], "total": 0}
    
    from sqlalchemy.orm import aliased
    cart = aliased(Carteirinha)
    
    from sqlalchemy import and_
    
    query = (
        db.query(FaturamentoLote, cart.paciente.label("nome_beneficiario"))
        .outerjoin(cart, and_(
            FaturamentoLote.CodigoBeneficiario == cart.codigo_beneficiario,
            FaturamentoLote.user_id == cart.user_id,
            cart.id_convenio == lote.id_convenio
        ))
        .filter(FaturamentoLote.id_lote == lote.id_lote)
    )
    if not current_user.is_admin:
        query = query.filter(FaturamentoLote.user_id == current_user.id)
        
    total = query.count()
    rows = query.order_by(FaturamentoLote.created_at.desc()).offset(skip).limit(limit).all()
    
    data = []
    for fat, nome_ben in rows:
        dic = {c.name: getattr(fat, c.name) for c in fat.__table__.columns}
        dic["nome_beneficiario"] = nome_ben or dic.get("CodigoBeneficiario", "")
        data.append(dic)
    
    return {"data": data, "total": total}

class ProtocoloLoteRequest(BaseModel):
    protocolo: str

@router.put("/{id_lote}/protocolo")
def update_lote_protocolo(
    id_lote: int,
    request: ProtocoloLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Grava o protocolo/capa do lote no portal (ex.: ConferenciaFaturamento IPASGO)."""
    protocolo = request.protocolo.strip()
    if not protocolo or not protocolo.isdigit():
        raise HTTPException(status_code=400, detail="Informe o protocolo numérico do lote no portal.")

    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    lote.protocolo = protocolo
    db.commit()
    return {"id_lote": lote.id_lote, "protocolo": lote.protocolo}


# ══════════════ Anexos por guia (OP19 — ConferenciaFaturamento IPASGO) ══════════════
# PDFs nomeados "...<senha11>.pdf" (ex.: rhaviluizgoncalvesdacruz24768401331.pdf):
# a senha tem 11 dígitos e a guia são exatamente os 8 primeiros dígitos.

ANEXO_MAX_BYTES = 5 * 1024 * 1024  # mesmo limite do fluxo de autorização (op1)
_ANEXO_SENHA_RE = re.compile(r"(?<!\d)(\d{11})\.pdf$", re.IGNORECASE)


def _parse_anexo_filename(nome):
    """Retorna (senha, guia) do nome do arquivo, ou (None, None) se inválido."""
    match = _ANEXO_SENHA_RE.search(nome or "")
    if not match:
        return None, None
    senha = match.group(1)
    return senha, senha[:8]


def _expandir_caminhos_anexos(caminhos):
    """
    Expande as entradas fornecidas: PASTA -> todos os .pdf do seu nível
    (ordenados); arquivo -> ele mesmo. Retorna (arquivos, resumo, ignorados).
    """
    arquivos, resumo, ignorados = [], [], 0
    for entrada in caminhos:
        entrada = str(entrada or "").strip().strip('"').strip()
        if not entrada:
            continue
        entrada = os.path.normpath(entrada)
        if os.path.isdir(entrada):
            # Somente o nível da pasta (subpastas NÃO são varridas)
            with os.scandir(entrada) as it:
                itens = list(it)
            encontrados = sorted(
                e.path for e in itens
                if e.is_file() and e.name.lower().endswith(".pdf")
            )
            ignorados += len(itens) - len(encontrados)
            arquivos.extend(encontrados)
            resumo.append({"entrada": entrada, "arquivos": len(encontrados)})
        else:
            arquivos.append(entrada)
            resumo.append({"entrada": entrada, "arquivos": 1})
    # Deduplica preservando a ordem (pasta + arquivo avulso repetido)
    arquivos = list(dict.fromkeys(arquivos))
    return arquivos, resumo, ignorados


def _analisar_caminho_anexo(db: Session, lote: LoteConvenio, caminho: str) -> dict:
    """Valida 1 caminho de anexo: nome (senha/guia), arquivo, guia no lote."""
    caminho = str(caminho or "").strip().strip('"')
    nome = os.path.basename(caminho)
    senha, guia = _parse_anexo_filename(nome)

    resultado = {"caminho": caminho, "arquivo": nome, "senha": senha, "guia": guia,
                 "valido": False, "motivo": None}

    if not caminho:
        resultado["motivo"] = "Caminho vazio."
        return resultado
    if os.path.isdir(caminho):
        resultado["motivo"] = "Caminho é uma pasta (esperado um PDF)."
        return resultado
    if senha is None:
        resultado["motivo"] = "Nome não termina com <senha11 dígitos>.pdf"
        return resultado
    if not os.path.isfile(caminho):
        resultado["motivo"] = "Arquivo não encontrado (o worker roda na mesma máquina dos PDFs)."
        return resultado
    if not nome.lower().endswith(".pdf"):
        resultado["motivo"] = "Somente PDF."
        return resultado
    tamanho = os.path.getsize(caminho)
    if tamanho == 0:
        resultado["motivo"] = "Arquivo vazio."
        return resultado
    if tamanho > ANEXO_MAX_BYTES:
        resultado["motivo"] = f"Arquivo de {tamanho/1024/1024:.1f}MB (limite 5MB)."
        return resultado

    no_lote = db.query(FaturamentoLote.id).filter(
        FaturamentoLote.id_lote == lote.id_lote,
        FaturamentoLote.Guia == guia,
    ).first()
    if not no_lote:
        resultado["motivo"] = f"Guia {guia} não pertence ao lote."
        return resultado

    # Consistência senha↔guia na base (aviso, não bloqueia: a guia do lote é a âncora)
    bg = db.query(BaseGuia.guia).filter(BaseGuia.senha == senha, BaseGuia.id_convenio == 6).first()
    if bg and bg[0] and bg[0].lstrip("0") != guia.lstrip("0"):
        resultado["motivo"] = f"Senha {senha} pertence à guia {bg[0]} na base (arquivo nomeado errado?)."
        return resultado

    resultado["valido"] = True
    return resultado


class AnexosLoteRequest(BaseModel):
    caminhos: List[str]


@router.post("/{id_lote}/anexos/analisar")
def analisar_anexos_lote(
    id_lote: int,
    request: AnexosLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Pré-visualiza o parse/validação dos caminhos de anexo (sem criar jobs).

    Aceita PASTAS (expande para todos os .pdf do nível) e/ou arquivos avulsos.
    """
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    arquivos, resumo, ignorados = _expandir_caminhos_anexos(request.caminhos)
    if not arquivos:
        raise HTTPException(
            status_code=400,
            detail="Nenhum PDF encontrado nas entradas informadas (pastas são varridas no nível atual).",
        )
    data = [_analisar_caminho_anexo(db, lote, c) for c in arquivos]
    return {
        "data": data,
        "validos": sum(1 for d in data if d["valido"]),
        "expansao": resumo,
        "ignorados_nao_pdf": ignorados,
    }


@router.post("/{id_lote}/anexos")
def criar_jobs_anexos_lote(
    id_lote: int,
    request: AnexosLoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cria 1 job OP19 (upload do PDF) por caminho válido. Exige protocolo no lote."""
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    protocolo = (lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(
            status_code=400,
            detail="Lote sem protocolo informado. Grave o protocolo (capa) antes de anexar arquivos.",
        )

    # Pasta -> todos os PDFs do nível (sem subpastas); cada PDF vira 1 job
    arquivos, _resumo, _ignorados = _expandir_caminhos_anexos(request.caminhos)
    if not arquivos:
        raise HTTPException(
            status_code=400,
            detail="Nenhum PDF encontrado nas entradas informadas (pastas são varridas no nível atual, sem subpastas).",
        )

    validos, rejeitados = [], []
    for caminho in arquivos:
        analise = _analisar_caminho_anexo(db, lote, caminho)
        if analise["valido"]:
            validos.append(analise)
        else:
            rejeitados.append({"arquivo": analise["arquivo"], "motivo": analise["motivo"]})

    # Se havia job OP19 anterior preso em pending/processing para o mesmo arquivo+protocolo,
    # cancela o antigo (evita travar reenvio quando um job anterior falhou/foi interrompido)
    caminhos_novos = {os.path.normpath(a["caminho"]) for a in validos}
    if caminhos_novos:
        pendentes = db.query(Job).filter(
            Job.rotina.in_(["19", "op19_anexar_arquivo_guia"]),
            Job.status.in_(["pending", "running", "processing"]),
        ).all()
        for j_antigo in pendentes:
            try:
                p_raw = j_antigo.params
                p_dict = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
                if (
                    str(p_dict.get("protocolo") or "").strip() == protocolo
                    and os.path.normpath(str(p_dict.get("caminho_arquivo") or "")) in caminhos_novos
                ):
                    j_antigo.status = "error"
                    j_antigo.error_message = "Substituído por novo envio de anexo (OP19)."
                    j_antigo.locked_by = None
                    j_antigo.attempts = j_antigo.max_attempts or 3
            except Exception:
                pass

    for anexo in validos:
        db.add(Job(
            carteirinha_id=None,
            id_convenio=6,
            rotina="19",  # OP19: AnexarArquivoGuia (ConferenciaFaturamento)
            params=json.dumps({
                "protocolo": protocolo,
                "numeroGuiaOperadora": anexo["guia"],
                "senha_guia": anexo["senha"],
                "caminho_arquivo": anexo["caminho"],
                "nome_arquivo": anexo["arquivo"],
            }),
            status="pending",
            user_id=lote.user_id or current_user.id,
        ))
    db.commit()

    return {"criados": len(validos), "rejeitados": rejeitados}


class ReprocessarAnexosRequest(BaseModel):
    job_ids: Optional[List[int]] = None


@router.get("/{id_lote}/anexos/status")
def status_anexos_lote(
    id_lote: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Consulta o status consolidado de envio de anexos (OP19) deste lote."""
    from services.worker_service import sync_and_abort_op19_jobs, is_bloqueio_ipasgo, is_instabilidade_portal, e_cascata_op19
    sync_and_abort_op19_jobs(db)
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    protocolo = str(lote.protocolo or "").strip()
    if not protocolo:
        return {
            "total": 0, "concluidos": 0, "processando": 0, "pendentes": 0,
            "erros": 0, "abortados": 0, "bloqueio_detectado": False,
            "sessao_rejeitada_detectada": False, "itens": []
        }

    jobs_query = db.query(Job).filter(
        Job.rotina.in_(["19", "op19_anexar_arquivo_guia"]),
        Job.id_convenio == 6
    )
    if not current_user.is_admin:
        jobs_query = jobs_query.filter(Job.user_id == current_user.id)

    todos_jobs = jobs_query.order_by(Job.id.desc()).all()

    itens = []
    concluidos = 0
    processando = 0
    pendentes = 0
    erros = 0
    abortados = 0
    bloqueio_detectado = False
    sessao_rejeitada_detectada = False

    for j in todos_jobs:
        p_raw = j.params
        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
        if str(p.get("protocolo") or "").strip() != protocolo:
            continue

        attempts = j.attempts or 0
        max_attempts = j.max_attempts or 3
        err_msg = str(j.error_message or "")

        # Só classifica como bloqueio/sessão job que de fato FALHOU contra o
        # portal. Jobs reprocessados com sucesso carregavam error_message
        # residual, e mensagens de cascata ("não foi tentado") traziam a palavra
        # "bloqueio" sem ter havido bloqueio — ambos mantinham o banner ativo.
        is_block = False
        is_sessao = False
        if j.status == "error" and not e_cascata_op19(err_msg):
            is_block = is_bloqueio_ipasgo(err_msg)
            is_sessao = not is_block and is_instabilidade_portal(err_msg)
            if is_block:
                bloqueio_detectado = True
            elif is_sessao:
                sessao_rejeitada_detectada = True

        is_aborted = (j.status == "error" and (attempts >= max_attempts or "abortad" in err_msg.lower() or is_block or is_sessao))

        if j.status == "success":
            concluidos += 1
        elif j.status in ("running", "processing"):
            processando += 1
        elif j.status == "pending":
            pendentes += 1
        elif j.status == "error":
            erros += 1
            if is_aborted:
                abortados += 1

        itens.append({
            "id": j.id,
            "guia": p.get("numeroGuiaOperadora") or p.get("guia"),
            "senha": p.get("senha_guia") or p.get("senha"),
            "arquivo": p.get("nome_arquivo") or (os.path.basename(str(p.get("caminho_arquivo") or "")) if p.get("caminho_arquivo") else "-"),
            "caminho": p.get("caminho_arquivo"),
            "status": j.status,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "error_message": err_msg,
            "is_abortado": is_aborted,
            "bloqueio": is_block,
            "sessao_rejeitada": is_sessao,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        })

    return {
        "total": len(itens),
        "concluidos": concluidos,
        "processando": processando,
        "pendentes": pendentes,
        "erros": erros,
        "abortados": abortados,
        "bloqueio_detectado": bloqueio_detectado,
        "sessao_rejeitada_detectada": sessao_rejeitada_detectada,
        "itens": itens
    }


@router.post("/{id_lote}/anexos/reprocessar")
def reprocessar_anexos_lote(
    id_lote: int,
    request: Optional[ReprocessarAnexosRequest] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Reenvia para a fila (status='pending', attempts=0) os anexos pendentes ou abortados."""
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    protocolo = str(lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(status_code=400, detail="Lote sem protocolo.")

    query = db.query(Job).filter(
        Job.rotina.in_(["19", "op19_anexar_arquivo_guia"]),
        Job.id_convenio == 6
    )
    if not current_user.is_admin:
        query = query.filter(Job.user_id == current_user.id)

    specified_ids = set(request.job_ids) if request and request.job_ids else None

    candidate_jobs = query.all()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    reprocessados = 0

    for j in candidate_jobs:
        p_raw = j.params
        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
        if str(p.get("protocolo") or "").strip() != protocolo:
            continue
        if specified_ids is not None and j.id not in specified_ids:
            if j.status == "error" and j.error_message and not str(j.error_message).lower().startswith("abortado -"):
                j.error_message = f"Abortado - {j.error_message}"[:1000]
            continue
        # Não reprocessa o que já teve sucesso a menos que seja explicitamente pedido por job_ids
        if j.status == "success" and not specified_ids:
            continue

        j.status = "pending"
        j.attempts = 0
        j.locked_by = None
        j.error_message = None
        j.updated_at = now
        reprocessados += 1

    db.commit()
    try:
        from cache import cache
        cache.clear()
    except Exception:
        pass
    return {"message": f"{reprocessados} job(s) de anexo reenviados para a fila.", "reprocessados": reprocessados}


@router.post("/{id_lote}/anexos/abortar")
def abortar_anexos_lote(
    id_lote: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Aborta manualmente os jobs de anexo pendentes ou em processamento do lote (ex.: IPASGO bloqueado)."""
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if not current_user.is_admin and lote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este lote.")

    protocolo = str(lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(status_code=400, detail="Lote sem protocolo.")

    query = db.query(Job).filter(
        Job.rotina.in_(["19", "op19_anexar_arquivo_guia"]),
        Job.id_convenio == 6,
        Job.status.in_(["pending", "processing", "running", "error"])
    )
    if not current_user.is_admin:
        query = query.filter(Job.user_id == current_user.id)

    candidate_jobs = query.all()
    from datetime import datetime, timezone
    from models import Worker
    now = datetime.now(timezone.utc)
    abortados = 0
    aborted_ids = []

    for j in candidate_jobs:
        p_raw = j.params
        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
        if str(p.get("protocolo") or "").strip() != protocolo:
            continue
        aborted_ids.append(j.id)
        if j.status in ("pending", "processing", "running") or (j.attempts or 0) < 3:
            j.status = "error"
            j.error_message = "Abortado manualmente (bloqueio do IPASGO). Pendente para reprocessamento posterior."
            j.locked_by = None
            j.attempts = max(3, j.attempts or 0)
            j.updated_at = now
            abortados += 1

    if aborted_ids:
        busy_workers = db.query(Worker).filter(Worker.current_job_id.in_(aborted_ids)).all()
        for w in busy_workers:
            w.status = "idle"
            w.current_job_id = None
            w.command = "restart"

    db.commit()
    try:
        from cache import cache
        cache.clear()
    except Exception:
        pass
    return {"message": f"{abortados} job(s) de anexo abortados. Ficaram pendentes para reprocessamento.", "abortados": abortados}


class UpdateLoteItemRequest(BaseModel):
    status_conferencia: Optional[int] = None
    auto_envio: Optional[bool] = False
    data_realizacao: Optional[str] = None

@router.put("/itens/{id}")
def update_lote_item(
    id: int,
    request: UpdateLoteItemRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    item = db.query(FaturamentoLote).filter(FaturamentoLote.id == id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item de faturamento não encontrado.")
    if not current_user.is_admin and item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para este item.")
        
    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == item.id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote associado não encontrado.")

    # Status ANTES de qualquer mutação: o espelhamento abaixo sobrescreveria
    # "I" por "A", e a guarda do auto_envio precisa enxergar o status original.
    status_letra_original = item.status_conferencia

    if request.data_realizacao is not None:
        try:
            item.dataRealizacao = date.fromisoformat(request.data_realizacao)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido. Use AAAA-MM-DD.")
            
    if request.status_conferencia is not None:
        item.StatusConferencia = request.status_conferencia
        # Espelha o status legado (67/78/82) na letra do novo portal (A/P/R)
        item.status_conferencia = {67: "A", 78: "P", 82: "R"}.get(request.status_conferencia)

    # Auto envio ao portal (IPASGO): job OP18 no módulo ConferenciaFaturamento.
    # Item Inapto (sem data de realização) não é enviável ao portal — conferido
    # pelo status ORIGINAL ("I" ou sem data), pois o espelhamento acima já pode
    # ter sobrescrito a letra para "A". A exceção ocorre antes do commit, então
    # nem o status nem o job são gravados.
    if request.auto_envio and lote.id_convenio == 6:
        if status_letra_original == "I" or (
            item.dataRealizacao is None and request.data_realizacao is None
        ):
            raise HTTPException(
                status_code=400,
                detail="Item Inapto (sem data de realização) não pode ser enviado ao portal.",
            )
        protocolo = (lote.protocolo or "").strip()
        if not protocolo:
            raise HTTPException(
                status_code=400,
                detail="Lote sem protocolo informado. Grave o protocolo (capa) do lote antes de enviar ao portal.",
            )
        import json
        status_portal = {67: "Aceito", 82: "Recusado", 78: "Pendente"}.get(
            item.StatusConferencia, "Aceito"
        )
        new_job = Job(
            carteirinha_id=None,
            id_convenio=6,
            rotina="18",  # OP18: ConferirItem + EditarDataRealizacao (novo portal)
            params=json.dumps({
                "protocolo": protocolo,
                "codigoPrestador": lote.cod_prestador,
                "idServico": item.detalheId,
                "idDetalhe": None,
                "status": status_portal,
                "dataRealizacao": item.dataRealizacao.strftime("%d/%m/%Y") if item.dataRealizacao else None,
            }),
            status="pending",
            user_id=current_user.id
        )
        db.add(new_job)

    db.commit()
    return {"message": "Item atualizado com sucesso."}
