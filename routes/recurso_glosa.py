import os
import json
"""
Rotas do módulo Recurso de Glosa (IPASGO).

- Lotes do convênio IPASGO com protocolo (capa) e agregados dos itens;
- Itens importados pela OP10 com filtros (guia, paciente, data, status);
- Catálogo de justificativas (padrão motivos_faltas) e aplicação em massa;
- Envio das justificativas ao portal via job da OP16.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, cast, Text
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, get_effective_user_id
from models import (
    Job,
    FaturamentoLote,
    LoteConvenio,
    RecursoGlosaItem,
    UserConvenio,
    JustificativaGlosa,
)

router = APIRouter(
    prefix="/recurso-glosa",
    tags=["Recurso de Glosa"]
)

ID_CONVENIO_IPASGO = 6

STATUS_PENDENTE = "Pendente"
STATUS_JUSTIFICATIVA = "Justificativa Definida"
STATUS_ENVIADO = "Enviado"

ROTINA_IMPORTAR = "op10_recurso_glosa"
ROTINA_ENVIAR = "op16_enviar_recurso_glosa"


def _query_lotes_tenant(db: Session, current_user):
    """Lotes do IPASGO visíveis para o usuário (mesma guarda do router /lotes)."""
    query = db.query(LoteConvenio).filter(LoteConvenio.id_convenio == ID_CONVENIO_IPASGO)
    if not current_user.is_admin:
        query = query.filter(LoteConvenio.user_id == current_user.id)
    return query


def _lote_do_usuario(db: Session, current_user, id_lote: int) -> LoteConvenio:
    lote = _query_lotes_tenant(db, current_user).filter(LoteConvenio.id_lote == id_lote).first()
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado para este usuário.")
    return lote


# ══════════════════════════════ Lotes ══════════════════════════════

@router.get("/lotes")
def list_lotes(
    limit: int = 100,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    lotes = (
        _query_lotes_tenant(db, current_user)
        .order_by(LoteConvenio.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    ids = [l.id_lote for l in lotes]

    # Agregados dos itens em uma passada (counts por status + somas de valores)
    agregados = {}
    if ids:
        status_rows = db.query(
            RecursoGlosaItem.id_lote,
            RecursoGlosaItem.status,
            func.count(RecursoGlosaItem.id),
            func.coalesce(func.sum(RecursoGlosaItem.valor_unitario_glosado * RecursoGlosaItem.quantidade_glosada), 0.0),
            func.coalesce(func.sum(RecursoGlosaItem.valor_recurso), 0.0),
        ).filter(RecursoGlosaItem.id_lote.in_(ids)).group_by(
            RecursoGlosaItem.id_lote, RecursoGlosaItem.status
        ).all()

        for id_lote, status, count, glosado, recurso in status_rows:
            agg = agregados.setdefault(id_lote, {
                "total_itens": 0, "com_justificativa": 0, "enviados": 0,
                "valor_glosado": 0.0, "valor_recurso": 0.0,
            })
            agg["total_itens"] += count
            agg["valor_glosado"] += float(glosado or 0)
            agg["valor_recurso"] += float(recurso or 0)
            if status == STATUS_ENVIADO:
                agg["enviados"] += count
                agg["com_justificativa"] += count
            elif status == STATUS_JUSTIFICATIVA:
                agg["com_justificativa"] += count

    data = []
    for lote in lotes:
        agg = agregados.get(lote.id_lote, {
            "total_itens": 0, "com_justificativa": 0, "enviados": 0,
            "valor_glosado": 0.0, "valor_recurso": 0.0,
        })
        data.append({
            "id_lote": lote.id_lote,
            "numero_lote": lote.numero_lote,
            "protocolo": lote.protocolo,
            "cod_prestador": lote.cod_prestador,
            "status": lote.status,
            "data_inicio": lote.data_inicio,
            "data_fim": lote.data_fim,
            "created_at": lote.created_at,
            **agg,
        })
    return {"data": data, "total": len(data)}


class ProtocoloUpdate(BaseModel):
    protocolo: str


@router.put("/lotes/{id_lote}/protocolo")
def update_protocolo(
    id_lote: int,
    req: ProtocoloUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    protocolo = req.protocolo.strip()
    if not protocolo or not protocolo.isdigit():
        raise HTTPException(status_code=400, detail="Informe o protocolo numérico do lote no portal (ex.: 895854).")

    lote = _lote_do_usuario(db, current_user, id_lote)
    lote.protocolo = protocolo
    # Mantém os itens já importados coerentes com a capa
    db.query(RecursoGlosaItem).filter(
        RecursoGlosaItem.id_lote == lote.id_lote
    ).update({"protocolo": protocolo}, synchronize_session=False)
    db.commit()
    return {"id_lote": lote.id_lote, "protocolo": lote.protocolo}


# ══════════════════════════════ Itens ══════════════════════════════

@router.get("/itens")
def list_itens(
    id_lote: int,
    guia: Optional[str] = None,
    paciente: Optional[str] = None,
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    status: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    _lote_do_usuario(db, current_user, id_lote)

    query = (
        db.query(RecursoGlosaItem, FaturamentoLote.dataRealizacao)
        .outerjoin(
            FaturamentoLote,
            RecursoGlosaItem.id_faturamento_lote == FaturamentoLote.id,
        )
        .filter(RecursoGlosaItem.id_lote == id_lote)
    )

    if guia and guia.strip():
        query = query.filter(RecursoGlosaItem.guia.ilike(f"%{guia.strip()}%"))
    if paciente and paciente.strip():
        termo = f"%{paciente.strip()}%"
        query = query.filter(or_(
            RecursoGlosaItem.nome_segurado.ilike(termo),
            RecursoGlosaItem.codigo_segurado.ilike(termo),
        ))
    if data_inicio:
        query = query.filter(FaturamentoLote.dataRealizacao >= data_inicio)
    if data_fim:
        query = query.filter(FaturamentoLote.dataRealizacao <= data_fim)
    if status and status.strip():
        query = query.filter(RecursoGlosaItem.status == status.strip())

    total = query.count()
    rows = (
        query.order_by(RecursoGlosaItem.guia, RecursoGlosaItem.chave)
        .offset(skip)
        .limit(limit)
        .all()
    )

    data = []
    for item, data_realizacao in rows:
        data.append({
            "id": item.id,
            "id_lote": item.id_lote,
            "chave": item.chave,
            "chave_ut_serv": item.chave_ut_serv,
            "codigo_segurado": item.codigo_segurado,
            "nome_segurado": item.nome_segurado,
            "guia": item.guia,
            "codigo_amb": item.codigo_amb,
            "descricao_servico": item.descricao_servico,
            "quantidade_glosada": item.quantidade_glosada,
            "valor_unitario_glosado": item.valor_unitario_glosado,
            "valor_recurso": item.valor_recurso,
            "justificativa_portal": item.justificativa_portal,
            "justificativa_id": item.justificativa_id,
            "justificativa_texto": item.justificativa_texto,
            "status": item.status,
            "data_envio": item.data_envio,
            "data_realizacao": data_realizacao,
        })
    return {"data": data, "total": total}


class AplicarJustificativaRequest(BaseModel):
    id_lote: int
    item_ids: Optional[List[int]] = None  # ausente + todos=False -> nenhum; informe ids OU todos
    todos: bool = False  # aplica a TODOS os itens não enviados do lote
    justificativa_id: int


@router.post("/itens/justificativa")
def aplicar_justificativa(
    req: AplicarJustificativaRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    _lote_do_usuario(db, current_user, req.id_lote)

    efetivo_id = get_effective_user_id(current_user)
    justificativa = db.query(JustificativaGlosa).filter(
        JustificativaGlosa.id == req.justificativa_id,
        JustificativaGlosa.status == "Ativo",
        or_(JustificativaGlosa.user_id == efetivo_id, JustificativaGlosa.user_id.is_(None)),
    ).first()
    if not justificativa:
        raise HTTPException(status_code=404, detail="Justificativa não encontrada ou inativa.")

    query = db.query(RecursoGlosaItem).filter(RecursoGlosaItem.id_lote == req.id_lote)
    if req.todos or not req.item_ids:
        # Em massa: todos os itens ainda não enviados do lote
        query = query.filter(RecursoGlosaItem.status != STATUS_ENVIADO)
    else:
        query = query.filter(RecursoGlosaItem.id.in_(req.item_ids))
        query = query.filter(RecursoGlosaItem.status != STATUS_ENVIADO)

    itens = query.all()
    if not itens:
        raise HTTPException(status_code=400, detail="Nenhum item elegível para receber a justificativa.")

    for item in itens:
        item.justificativa_id = justificativa.id
        item.justificativa_texto = justificativa.descricao
        item.status = STATUS_JUSTIFICATIVA
    db.commit()

    return {"message": "Justificativa aplicada.", "itens_atualizados": len(itens)}


# ════════════════════════ Justificativas (catálogo) ════════════════════════

class JustificativaCreate(BaseModel):
    descricao: str

class JustificativaUpdate(BaseModel):
    descricao: Optional[str] = None
    status: Optional[str] = None


@router.get("/justificativas")
def list_justificativas(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    efetivo_id = get_effective_user_id(current_user)
    return (
        db.query(JustificativaGlosa)
        .filter(
            JustificativaGlosa.status == "Ativo",
            or_(JustificativaGlosa.user_id == efetivo_id, JustificativaGlosa.user_id.is_(None)),
        )
        .order_by(JustificativaGlosa.descricao)
        .all()
    )


@router.post("/justificativas")
def create_justificativa(
    req: JustificativaCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    descricao = req.descricao.strip()
    if len(descricao) < 10:
        raise HTTPException(status_code=400, detail="Descreva a justificativa (mínimo 10 caracteres).")

    justificativa = JustificativaGlosa(
        descricao=descricao,
        user_id=get_effective_user_id(current_user),
    )
    db.add(justificativa)
    db.commit()
    db.refresh(justificativa)
    return justificativa


@router.put("/justificativas/{justificativa_id}")
def update_justificativa(
    justificativa_id: int,
    req: JustificativaUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    efetivo_id = get_effective_user_id(current_user)
    justificativa = db.query(JustificativaGlosa).filter(
        JustificativaGlosa.id == justificativa_id,
        or_(JustificativaGlosa.user_id == efetivo_id, JustificativaGlosa.user_id.is_(None)),
    ).first()
    if not justificativa:
        raise HTTPException(status_code=404, detail="Justificativa não encontrada.")

    if req.descricao is not None:
        descricao = req.descricao.strip()
        if len(descricao) < 10:
            raise HTTPException(status_code=400, detail="Descreva a justificativa (mínimo 10 caracteres).")
        justificativa.descricao = descricao
    if req.status is not None:
        justificativa.status = req.status
    db.commit()
    db.refresh(justificativa)
    return justificativa


@router.delete("/justificativas/{justificativa_id}")
def delete_justificativa(
    justificativa_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    efetivo_id = get_effective_user_id(current_user)
    justificativa = db.query(JustificativaGlosa).filter(
        JustificativaGlosa.id == justificativa_id,
        JustificativaGlosa.user_id == efetivo_id,
    ).first()
    if not justificativa:
        raise HTTPException(status_code=404, detail="Justificativa não encontrada.")
    justificativa.status = "Inativo"
    db.commit()
    return {"message": "Justificativa desativada com sucesso."}


# ════════════════════════ Envio (job OP16) ════════════════════════

class EnviarRequest(BaseModel):
    item_ids: Optional[List[int]] = None  # ausente -> todos os itens prontos (Justificativa Definida)


def _montar_servicos_envio(itens: list) -> list:
    """
    Converte os itens do banco no payload 'servicos' da OP16. valor_recurso
    nulo cai para valor_unitario_glosado x quantidade_glosada.
    """
    def _valor(v):
        numero = round(float(v or 0), 2)
        return int(numero) if numero.is_integer() else numero

    servicos = []
    for item in itens:
        quantidade = item.quantidade_glosada or 1
        valor_recurso = item.valor_recurso
        if valor_recurso is None:
            valor_recurso = round(float(item.valor_unitario_glosado or 0) * quantidade, 2)
        servicos.append({
            "chave": item.chave,
            "chave_ut_serv": item.chave_ut_serv or item.chave,
            "quantidade_glosada": quantidade,
            "valor_unitario_glosado": item.valor_unitario_glosado or 0,
            "valor_recurso": _valor(valor_recurso),
            "justificativa": (item.justificativa_texto or "").strip(),
        })
    return servicos


@router.post("/lotes/{id_lote}/enviar")
def enviar_recurso(
    id_lote: int,
    req: EnviarRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    lote = _lote_do_usuario(db, current_user, id_lote)

    protocolo = (lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(
            status_code=400,
            detail="Informe o protocolo do lote antes de enviar (coluna Protocolo).",
        )

    query = db.query(RecursoGlosaItem).filter(
        RecursoGlosaItem.id_lote == lote.id_lote,
        RecursoGlosaItem.status == STATUS_JUSTIFICATIVA,
    )
    if req.item_ids:
        query = query.filter(RecursoGlosaItem.id.in_(req.item_ids))

    itens = query.all()
    if not itens:
        raise HTTPException(
            status_code=400,
            detail="Nenhum item pronto para envio. Vincule justificativas aos itens primeiro.",
        )

    servicos = _montar_servicos_envio(itens)
    sem_justificativa = [s for s in servicos if not s["justificativa"]]
    if sem_justificativa:
        raise HTTPException(
            status_code=400,
            detail=f"{len(sem_justificativa)} item(ns) com justificativa vazia. Revincule a justificativa antes de enviar.",
        )

    # Credenciais do IPASGO (mesma injeção do fluxo standalone do POST /jobs/)
    efetivo_id = get_effective_user_id(current_user)
    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == efetivo_id,
        UserConvenio.id_convenio == ID_CONVENIO_IPASGO,
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(
            status_code=400,
            detail="Credenciais não cadastradas para o convênio IPASGO. Cadastre em Credenciais Portais.",
        )

    cod_prestador = (lote.cod_prestador or uconv.cod_prestador or "").strip()

    from services.job_service import MY_WEBHOOK_URL, _send_jobs_to_worker

    p_dict = {
        "protocolo": protocolo,
        "capa": protocolo,
        "id_lote_interno": lote.id_lote,
        "servicos": servicos,
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "cod_prestador": cod_prestador,
        "codigoPrestador": cod_prestador,
        "webhook_url": MY_WEBHOOK_URL,
    }

    job_payload = {
        "carteirinha_id": None,
        "id_convenio": ID_CONVENIO_IPASGO,
        "user_id": lote.user_id or current_user.id,
        "rotina": ROTINA_ENVIAR,
        "priority": 0,
        "params": p_dict,
        "max_attempts": 3,
    }

    _send_jobs_to_worker([job_payload], db=db)

    return {
        "message": "Envio de recurso de glosa acionado.",
        "id_lote": lote.id_lote,
        "protocolo": protocolo,
        "servicos": len(servicos),
    }


# ══════════════ Anexos do Recurso de Glosa (OP21) ══════════════

from routes.lotes import _expandir_caminhos_anexos, ANEXO_MAX_BYTES
from services.worker_service import (
    sync_and_abort_op21_jobs,
    is_bloqueio_ipasgo,
    is_instabilidade_portal,
    e_cascata_op21,
)


class AnexosRecursoGlosaRequest(BaseModel):
    caminhos: List[str]


class ReprocessarAnexosRecursoRequest(BaseModel):
    job_ids: Optional[List[int]] = None


def _analisar_caminho_anexo_recurso(caminho: str) -> dict:
    """Valida 1 caminho de anexo para recurso de glosa (qualquer PDF válido <= 5MB)."""
    caminho = str(caminho or "").strip().strip('"')
    nome = os.path.basename(caminho)

    resultado = {
        "caminho": caminho,
        "arquivo": nome,
        "valido": False,
        "motivo": None,
    }

    if not caminho:
        resultado["motivo"] = "Caminho vazio."
        return resultado
    if os.path.isdir(caminho):
        resultado["motivo"] = "Caminho é uma pasta (esperado um PDF)."
        return resultado
    if not os.path.isfile(caminho):
        resultado["motivo"] = "Arquivo não encontrado (o worker roda na mesma máquina dos PDFs)."
        return resultado
    if not nome.lower().endswith(".pdf"):
        resultado["motivo"] = "Somente PDF."
        return resultado
    try:
        tamanho = os.path.getsize(caminho)
    except Exception as e:
        resultado["motivo"] = f"Erro ao acessar arquivo: {e}"
        return resultado

    if tamanho == 0:
        resultado["motivo"] = "Arquivo vazio."
        return resultado
    if tamanho > ANEXO_MAX_BYTES:
        resultado["motivo"] = f"Arquivo de {tamanho/1024/1024:.1f}MB (limite 5MB)."
        return resultado

    resultado["valido"] = True
    return resultado


@router.post("/lotes/{id_lote}/anexos/analisar")
def analisar_anexos_recurso(
    id_lote: int,
    request: AnexosRecursoGlosaRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Pré-visualiza validação dos caminhos de anexo do recurso de glosa (sem criar jobs).
    Aceita PASTAS (expande para todos os .pdf do nível) e/ou arquivos avulsos.
    """
    lote = _lote_do_usuario(db, current_user, id_lote)
    arquivos, resumo, ignorados = _expandir_caminhos_anexos(request.caminhos)
    if not arquivos:
        raise HTTPException(
            status_code=400,
            detail="Nenhum PDF encontrado nas entradas informadas (pastas são varridas no nível atual).",
        )
    data = [_analisar_caminho_anexo_recurso(c) for c in arquivos]
    return {
        "data": data,
        "validos": sum(1 for d in data if d["valido"]),
        "expansao": resumo,
        "ignorados_nao_pdf": ignorados,
    }


@router.post("/lotes/{id_lote}/anexos")
def criar_jobs_anexos_recurso(
    id_lote: int,
    request: AnexosRecursoGlosaRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Cria 1 job OP21 por PDF válido para anexar ao protocolo do recurso de glosa."""
    lote = _lote_do_usuario(db, current_user, id_lote)
    protocolo = (lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(
            status_code=400,
            detail="Lote sem protocolo informado. Grave o protocolo (capa) antes de anexar arquivos.",
        )

    arquivos, _resumo, _ignorados = _expandir_caminhos_anexos(request.caminhos)
    if not arquivos:
        raise HTTPException(
            status_code=400,
            detail="Nenhum PDF encontrado nas entradas informadas.",
        )

    validos, rejeitados = [], []
    for caminho in arquivos:
        analise = _analisar_caminho_anexo_recurso(caminho)
        if analise["valido"]:
            validos.append(analise)
        else:
            rejeitados.append({"arquivo": analise["arquivo"], "motivo": analise["motivo"]})

    caminhos_novos = {os.path.normpath(a["caminho"]) for a in validos}
    if caminhos_novos:
        pendentes = db.query(Job).filter(
            Job.rotina.in_(["21", "op21_anexar_recurso_glosa"]),
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
                    j_antigo.error_message = "Substituído por novo envio de anexo (OP21)."
                    j_antigo.locked_by = None
                    j_antigo.attempts = j_antigo.max_attempts or 3
            except Exception:
                pass

    efetivo_id = get_effective_user_id(current_user)
    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == efetivo_id,
        UserConvenio.id_convenio == ID_CONVENIO_IPASGO,
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(
            status_code=400,
            detail="Credenciais não cadastradas para o convênio IPASGO. Cadastre em Credenciais Portais.",
        )

    cod_prestador = (lote.cod_prestador or uconv.cod_prestador or "").strip()
    from services.job_service import MY_WEBHOOK_URL, _send_jobs_to_worker

    job_payloads = []
    for anexo in validos:
        p_dict = {
            "protocolo": protocolo,
            "caminho_arquivo": anexo["caminho"],
            "nome_arquivo": anexo["arquivo"],
            "id_lote_interno": lote.id_lote,
            "login": uconv.login,
            "senha_criptografada": uconv.senha_criptografada,
            "cod_prestador": cod_prestador,
            "codigoPrestador": cod_prestador,
            "webhook_url": MY_WEBHOOK_URL,
        }
        job_payloads.append({
            "carteirinha_id": None,
            "id_convenio": ID_CONVENIO_IPASGO,
            "rotina": "21",
            "params": p_dict,
            "status": "pending",
            "user_id": lote.user_id or current_user.id,
            "priority": 0,
            "max_attempts": 3,
        })

    if job_payloads:
        _send_jobs_to_worker(job_payloads, db=db)
        # Inserção nova de jobs: derruba o cache da lista de /jobs/ do(s)
        # tenant(s) afetado(s) para o modal de importações exibi-los no
        # próximo fetch (sem esperar o TTL expirar).
        try:
            from cache import cache
            for uid in {current_user.id, lote.user_id or current_user.id}:
                cache.invalidate_resource(uid, "jobs")
        except Exception:
            pass

    return {"criados": len(validos), "rejeitados": rejeitados}


@router.get("/lotes/{id_lote}/anexos/status")
def status_anexos_recurso(
    id_lote: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Consulta o status consolidado de envio de anexos (OP21) deste recurso de glosa."""
    lote = _lote_do_usuario(db, current_user, id_lote)

    protocolo = str(lote.protocolo or "").strip()
    if not protocolo:
        return {
            "total": 0, "concluidos": 0, "processando": 0, "pendentes": 0,
            "erros": 0, "abortados": 0, "bloqueio_detectado": False,
            "sessao_rejeitada_detectada": False, "itens": []
        }

    try:
        sync_and_abort_op21_jobs(db, protocolo=protocolo)
    except Exception as e:
        print(f"[OP21 status] sync_and_abort falhou silenciosamente: {e}")

    jobs_query = db.query(Job).filter(
        Job.rotina.in_(["21", "op21_anexar_recurso_glosa"]),
        Job.id_convenio == ID_CONVENIO_IPASGO,
        or_(
            Job.params["protocolo"].astext == protocolo,
            cast(Job.params, Text).ilike(f'%"{protocolo}"%'),
        ),
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

        is_block = False
        is_sessao = False
        if j.status == "error" and not e_cascata_op21(err_msg):
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
            "protocolo": protocolo,
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


@router.post("/lotes/{id_lote}/anexos/reprocessar")
def reprocessar_anexos_recurso(
    id_lote: int,
    request: Optional[ReprocessarAnexosRecursoRequest] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Reenvia para a fila (status='pending', attempts=0) os anexos pendentes ou abortados."""
    lote = _lote_do_usuario(db, current_user, id_lote)
    protocolo = str(lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(status_code=400, detail="Lote sem protocolo.")

    efetivo_id = get_effective_user_id(current_user)
    uconv = db.query(UserConvenio).filter(
        UserConvenio.user_id == efetivo_id,
        UserConvenio.id_convenio == ID_CONVENIO_IPASGO,
    ).first()
    if not uconv or not uconv.login or not uconv.senha_criptografada:
        raise HTTPException(
            status_code=400,
            detail="Credenciais não cadastradas para o convênio IPASGO. Cadastre em Credenciais Portais.",
        )

    cod_prestador = (lote.cod_prestador or uconv.cod_prestador or "").strip()
    from services.job_service import MY_WEBHOOK_URL, _send_jobs_to_worker

    jobs_query = db.query(Job).filter(
        Job.rotina.in_(["21", "op21_anexar_recurso_glosa"]),
        Job.id_convenio == ID_CONVENIO_IPASGO,
        or_(
            Job.params["protocolo"].astext == protocolo,
            cast(Job.params, Text).ilike(f'%"{protocolo}"%'),
        ),
    )
    if not current_user.is_admin:
        jobs_query = jobs_query.filter(Job.user_id == current_user.id)

    todos_jobs = jobs_query.all()
    alvos = []
    job_ids_filtro = set(request.job_ids) if (request and request.job_ids) else None

    for j in todos_jobs:
        p_raw = j.params
        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
        if str(p.get("protocolo") or "").strip() != protocolo:
            continue
        if job_ids_filtro and j.id not in job_ids_filtro:
            continue
        if job_ids_filtro or j.status == "error":
            alvos.append(j)

    payloads_reenvio = []
    for j in alvos:
        j.status = "pending"
        j.attempts = 0
        j.error_message = None
        j.locked_by = None
        p_raw = j.params
        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
        p["login"] = uconv.login
        p["senha_criptografada"] = uconv.senha_criptografada
        p["cod_prestador"] = cod_prestador
        p["codigoPrestador"] = cod_prestador
        p["webhook_url"] = MY_WEBHOOK_URL
        j.params = p
        payloads_reenvio.append({
            "id": j.id,
            "carteirinha_id": j.carteirinha_id,
            "id_convenio": j.id_convenio,
            "rotina": j.rotina,
            "params": p,
            "status": "pending",
            "user_id": j.user_id,
            "priority": j.priority or 0,
            "max_attempts": j.max_attempts or 3,
        })

    db.commit()
    if payloads_reenvio:
        _send_jobs_to_worker(payloads_reenvio, db=db)

    return {"reprocessados": len(alvos)}


@router.post("/lotes/{id_lote}/anexos/abortar")
def abortar_anexos_recurso(
    id_lote: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Aborta todos os jobs OP21 pendentes deste recurso de glosa."""
    lote = _lote_do_usuario(db, current_user, id_lote)
    protocolo = str(lote.protocolo or "").strip()
    if not protocolo:
        raise HTTPException(status_code=400, detail="Lote sem protocolo.")

    jobs_query = db.query(Job).filter(
        Job.rotina.in_(["21", "op21_anexar_recurso_glosa"]),
        Job.id_convenio == ID_CONVENIO_IPASGO,
        or_(
            Job.params["protocolo"].astext == protocolo,
            cast(Job.params, Text).ilike(f'%"{protocolo}"%'),
        ),
        Job.status == "pending",
    )
    if not current_user.is_admin:
        jobs_query = jobs_query.filter(Job.user_id == current_user.id)

    pendentes = jobs_query.all()
    abortados = 0
    for j in pendentes:
        p_raw = j.params
        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
        if str(p.get("protocolo") or "").strip() != protocolo:
            continue
        j.status = "error"
        j.error_message = "Abortado pelo usuário."
        j.locked_by = None
        j.attempts = j.max_attempts or 3
        abortados += 1

    db.commit()
    return {"abortados": abortados}
