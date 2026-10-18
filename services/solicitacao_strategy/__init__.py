"""Strategy de solicitação de autorização — família complementar à integrador_strategy.

Enquanto `services.integrador_strategy` cobre integradores tipo 'agendamento',
este pacote direciona SOLICITAÇÕES DE AUTORIZAÇÃO individuais (form Nova
Solicitação / importação de planilha) ao integrador do convênio, resolvido via
`convenios.id_integrador` (espaço W) — ex.: CAESAN (convênio 16 → integrador
102) → rotina física `op1_autorizacao`.

Entrada canônica: rotina `solicitar_autorizacao` (POST /jobs/).
Convênios sem strategy de solicitação seguem o fluxo legado (fallback).
"""
from .registry import get_solicitacao_strategy, resolver_strategy_do_convenio  # noqa: F401
from .base import ROTINA_CANONICA, SolicitacaoStrategy  # noqa: F401
