"""Estratégia do integrador Evoluir (id_integrador=100, espaço W).

Domínio pacientes/PTS/faturamento: OPs canônicas registradas no catálogo
(op1/op4/op5/op6/op7). OPs do domínio agendamento (sincronizar/confirmar/
faltar) NÃO existem no catálogo → 403 automático (bloqueio D4, onda 2 no
plano para implementação futura no portal).
"""
from models import Job
from services.integrador_strategy.base import IntegradorAgendamentoStrategy


class EvoluirStrategy(IntegradorAgendamentoStrategy):
    id_integrador = 100
    sigla = "EVOLUIR"

    # OPs do domínio agendamento: base já responde 403 ("Operação não suportada
    # pelo integrador Evoluir.") — não sobrescrever até a onda 2.

    def consumir_resultado(self, job: Job, synced_counts: dict = None) -> None:
        from services.guias_sync_service import _consume_evoluir
        _consume_evoluir(self.db, job, synced_counts or {"jobs_processed": 0, "inserted": 0,
                                                         "updated": 0, "skipped": 0})
