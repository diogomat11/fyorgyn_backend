"""Registry de estratégias por integrador tipo 'agendamento'.

A chave é SEMPRE o id_integrador do ESPAÇO W (worker.integradores — alinhado ao
id_convenio canônico desde a migration 0102). Integrador sem classe, inativo ou
sem cadastro no worker → NullStrategy (403 em toda operação).
"""
from sqlalchemy.orm import Session

from models import WorkerIntegrador
from services.integrador_strategy.base import IntegradorAgendamentoStrategy, NullStrategy
from services.integrador_strategy.clmf import AbaClmfStrategy
from services.integrador_strategy.evoluir import EvoluirStrategy

_STRATEGY_CLASSES = {
    101: AbaClmfStrategy,
    100: EvoluirStrategy,
}


def get_strategy(db: Session, id_integrador: int) -> IntegradorAgendamentoStrategy:
    """Instancia a estratégia do integrador (espaço W), com fallback NullStrategy."""
    if id_integrador is None:
        return NullStrategy(db)
    ing = db.query(WorkerIntegrador).filter(
        WorkerIntegrador.id_integrador == id_integrador
    ).first()
    cls = _STRATEGY_CLASSES.get(id_integrador)
    if cls is None or not ing or not ing.ativo:
        return NullStrategy(db, id_integrador)
    if ing.tipo_operacao != "agendamento":
        return NullStrategy(db, id_integrador)
    return cls(db)


def resolver_integrador_do_convenio(db: Session, id_convenio: int):
    """id_convenio → id_integrador (espaço W) via convenios.id_integrador."""
    from services.integrador_strategy.canonico import resolver_integrador_do_convenio as _resolver
    ing = _resolver(db, id_convenio)
    return ing.id_integrador if ing else None
