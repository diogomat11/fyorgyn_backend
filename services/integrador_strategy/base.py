"""Contrato base das estratégias de integrador tipo 'agendamento'.

Cada integrador implementa como cria seus Jobs (params específicos do portal) e
como consume os resultados. O catálogo canônico (worker.integrador_operacoes)
define quais OPs estão expostas — ausência de canônica ativa = 403 (D4, camada 1).
"""
from abc import ABC, abstractmethod

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Job, WorkerIntegrador
from services.integrador_strategy.canonico import rotina_por_operacao_canonica


class IntegradorAgendamentoStrategy(ABC):
    id_integrador = None  # ESPAÇO W (worker.integradores)
    sigla = None

    def __init__(self, db: Session):
        self.db = db

    # ── Catálogo / capabilities ──
    @property
    def nome(self) -> str:
        ing = self.db.query(WorkerIntegrador).filter(
            WorkerIntegrador.id_integrador == self.id_integrador
        ).first()
        return ing.nome if ing else f"Integrador {self.id_integrador}"

    def capabilities(self) -> set:
        from services.integrador_strategy.canonico import operacoes_canonicas_ativas
        return set(operacoes_canonicas_ativas(self.db, self.id_integrador))

    def suporta(self, operacao_canonica: str) -> bool:
        return operacao_canonica in self.capabilities()

    def _rotina_para(self, operacao_canonica: str) -> str:
        """Traduz OP canônica → rotina física do integrador; 403 se não suportada."""
        op = rotina_por_operacao_canonica(self.db, self.id_integrador, operacao_canonica)
        if not op:
            raise HTTPException(
                status_code=403,
                detail=f"Operação não suportada pelo integrador {self.nome}."
            )
        return op.rotina

    def _id_convenio_canonico(self) -> int:
        """Convênio canônico do integrador (public.integradores.id_convenio, UNIQUE)."""
        from models import Integrador
        row = self.db.query(Integrador.id_convenio).filter(
            Integrador.id_integrador == self.id_integrador
        ).first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Integrador {self.sigla or self.id_integrador} sem convênio canônico mapeado."
            )
        return row[0]

    def _criar_job(self, operacao_canonica: str, user_id: int, params, worker_key=None) -> Job:
        """Cria o Job com a rotina física correta do integrador."""
        rotina = self._rotina_para(operacao_canonica)
        job = Job(
            id_convenio=self._id_convenio_canonico(),
            rotina=rotina,
            status="pending",
            params=params,
            user_id=user_id,
            **({"worker_key": worker_key} if worker_key else {})
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    # ── Domínio agendamento (implementação default = não suportada, 403) ──
    def sincronizar_agendamentos(self, user_id: int, data_inicio=None, data_fim=None,
                                 id_paciente: str = "0") -> Job:
        return self._criar_job("sincronizar_agendamentos", user_id,
                               {"data_inicio": data_inicio, "data_fim": data_fim,
                                "id_paciente": id_paciente})

    def confirmar_agendamentos(self, user_id: int, portal_ids: list, remover: bool = False, **kw) -> Job:
        raise HTTPException(
            status_code=403,
            detail=f"Operação não suportada pelo integrador {self.nome}."
        )

    def registrar_falta(self, user_id: int, portal_ids: list, id_paciente=None,
                        motivo_mapeado=None, doc_justificativa=None, **kw) -> Job:
        raise HTTPException(
            status_code=403,
            detail=f"Operação não suportada pelo integrador {self.nome}."
        )

    def remover_falta(self, user_id: int, portal_ids: list, id_paciente=None,
                      data_inicial=None, data_final=None, **kw) -> Job:
        raise HTTPException(
            status_code=403,
            detail=f"Operação não suportada pelo integrador {self.nome}."
        )

    # ── Consumo de resultados (chamado pelo guias_sync_service) ──
    @abstractmethod
    def consumir_resultado(self, job: Job) -> None:
        """Pós-processa result_data de um job concluído deste integrador."""
        raise NotImplementedError


class NullStrategy(IntegradorAgendamentoStrategy):
    """Integrador sem estratégia implementada — toda operação é bloqueada com 403."""

    def __init__(self, db: Session, id_integrador=None):
        super().__init__(db)
        self.id_integrador = id_integrador

    def capabilities(self) -> set:
        return set()

    def _rotina_para(self, operacao_canonica: str) -> str:
        raise HTTPException(
            status_code=403,
            detail="Integrador não habilitado para operações de agendamento."
        )

    def consumir_resultado(self, job: Job) -> None:
        pass
