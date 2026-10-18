"""Registry da família de solicitação: integrador (espaço W) → strategy.

Resolução sempre pela relação convenios.id_integrador — sem hardcode de
id_convenio. Integrador sem strategy registrada → None (fluxo legado).
"""
from typing import Optional

from sqlalchemy.orm import Session

from models import Convenio, WorkerIntegrador
from .caesan import CaesanSolicitacaoStrategy

_SOLICITACAO_CLASSES = {
    102: CaesanSolicitacaoStrategy,
}


def get_solicitacao_strategy(db: Session, id_integrador: Optional[int]):
    """Strategy de solicitação para o integrador (espaço W) ou None."""
    if id_integrador is None:
        return None
    cls = _SOLICITACAO_CLASSES.get(id_integrador)
    if cls is None:
        return None
    ing = db.query(WorkerIntegrador).filter(
        WorkerIntegrador.id_integrador == id_integrador
    ).first()
    if not ing or not ing.ativo:
        return None
    return cls(db)


def resolver_strategy_do_convenio(db: Session, id_convenio: Optional[int]):
    """Resolve a strategy de solicitação a partir do convênio via convenios.id_integrador."""
    if not id_convenio:
        return None
    row = db.query(Convenio.id_integrador).filter(Convenio.id_convenio == id_convenio).first()
    if not row or not row[0]:
        return None
    return get_solicitacao_strategy(db, row[0])
