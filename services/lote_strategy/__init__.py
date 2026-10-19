"""
Resolução da estratégia de lote por convênio.

Cadeia (mesma do plano integradores): convenios.id_integrador → catálogo de
strategies → fallback por id_convenio → legado (formato número/IPASGO).
Convênios não mapeados preservam o comportamento atual (OP13).
"""
from services.lote_strategy.ipasgo import IpasgoLoteStrategy
from services.lote_strategy.unimed_periodo import UnimedPeriodoLoteStrategy

# Integrador 3 = Unimed Goiânia / Intercâmbio (SGUCard) → lote por período
_INTEGRADOR_STRATEGIES = {
    3: UnimedPeriodoLoteStrategy,
}

_CONVENIO_STRATEGIES = {
    3: UnimedPeriodoLoteStrategy,   # UNIMED GOIANIA
    21: UnimedPeriodoLoteStrategy,  # UNIMED INTERCAMBIO (mesmo integrador 3)
    6: IpasgoLoteStrategy,          # IPASGO (formato atual: apenas data_fim)
}


def get_lote_strategy(db, id_convenio: int) -> IpasgoLoteStrategy | UnimedPeriodoLoteStrategy:
    """Resolve a strategy de lote do convênio (legado IPASGO se não mapeado)."""
    try:
        from models import Convenio
        conv = db.query(Convenio.id_integrador).filter(
            Convenio.id_convenio == id_convenio).first()
        id_integrador = conv[0] if conv else None
    except Exception:
        id_integrador = None

    cls = _INTEGRADOR_STRATEGIES.get(id_integrador) or _CONVENIO_STRATEGIES.get(id_convenio)
    return cls() if cls else IpasgoLoteStrategy()


__all__ = ["get_lote_strategy", "IpasgoLoteStrategy", "UnimedPeriodoLoteStrategy",
           "LoteStrategy"]
