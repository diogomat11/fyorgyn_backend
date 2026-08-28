"""Estratégias de integrador tipo 'agendamento' (plano docs/plano_integradores_agendamento.md).

Rotas falam OP canônica; a estratégia traduz para a rotina física do integrador
via catálogo em banco (worker.integrador_operacoes.operacao_canonica).
"""
from services.integrador_strategy.base import IntegradorAgendamentoStrategy, NullStrategy
from services.integrador_strategy.registry import get_strategy, resolver_integrador_do_convenio

__all__ = [
    "IntegradorAgendamentoStrategy",
    "NullStrategy",
    "get_strategy",
    "resolver_integrador_do_convenio",
]
