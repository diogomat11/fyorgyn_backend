"""Catálogo canônico de operações de integradores tipo 'agendamento'.

O nome canônico é o contrato cross-integrador (ex.: 'registrar_falta'); a rotina
física (ex.: 'op4_registrar_falta') é resolvida por integrador no catálogo em banco
(worker.integrador_operacoes.operacao_canonica — migration 0103).

Fonte de verdade de runtime = schema worker. Espelho admin = schema public.
"""
from sqlalchemy.orm import Session

from models import Convenio, WorkerIntegrador, WorkerIntegradorOperacao

# ── OPs canônicas do domínio AGENDAMENTO (matriz no plano §5.2) ──
OP_SINCRONIZAR_AGENDAMENTOS = "sincronizar_agendamentos"
OP_CONSULTAR_CARTEIRINHA = "consultar_carteirinha"
OP_CONFIRMAR_AGENDAMENTO = "confirmar_agendamento"
OP_REGISTRAR_FALTA = "registrar_falta"
OP_REMOVER_FALTA = "remover_falta"
OP_ATUALIZAR_RC = "atualizar_rc"

# ── OPs canônicas do domínio PACIENTES/FATURAMENTO (Evoluir) ──
OP_IMPORTAR_PACIENTES = "importar_pacientes"
OP_ATUALIZAR_DATA_PTS = "atualizar_data_pts"
OP_IMPORTAR_CORPO_CLINICO = "importar_corpo_clinico"
OP_BAIXAR_FATURADOS = "baixar_faturados"
OP_CONSULTAR_DOCUMENTOS = "consultar_documentos"

TIPO_AGENDAMENTO = "agendamento"


def resolver_integrador_do_convenio(db: Session, id_convenio: int) -> WorkerIntegrador:
    """Resolve o integrador (espaço W) de um convênio via convenios.id_integrador.

    Retorna None se o convênio não tiver integrador ou não existir."""
    if id_convenio is None:
        return None
    id_integrador = db.query(Convenio.id_integrador).filter(
        Convenio.id_convenio == id_convenio
    ).first()
    if not id_integrador or not id_integrador[0]:
        return None
    return db.query(WorkerIntegrador).filter(
        WorkerIntegrador.id_integrador == id_integrador[0]
    ).first()


def operacoes_canonicas_ativas(db: Session, id_integrador: int) -> list:
    """OPs canônicas ativas de um integrador (rotina → canônica) no catálogo worker."""
    rows = db.query(WorkerIntegradorOperacao).filter(
        WorkerIntegradorOperacao.id_integrador == id_integrador,
        WorkerIntegradorOperacao.ativo == True,  # noqa: E712
        WorkerIntegradorOperacao.operacao_canonica.isnot(None)
    ).all()
    return sorted(r.operacao_canonica for r in rows if r.operacao_canonica)


def rotina_por_operacao_canonica(db: Session, id_integrador: int, operacao_canonica: str):
    """Retorna (rotina física, operacao) ativa para a OP canônica do integrador, ou None."""
    return db.query(WorkerIntegradorOperacao).filter(
        WorkerIntegradorOperacao.id_integrador == id_integrador,
        WorkerIntegradorOperacao.operacao_canonica == operacao_canonica,
        WorkerIntegradorOperacao.ativo == True  # noqa: E712
    ).first()
