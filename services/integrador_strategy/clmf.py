"""Estratégia do integrador ABA CLMF (id_integrador=101, espaço W).

Mantém paridade 1:1 com a lógica que vivia hardcoded em routes/agendamentos.py
(sincronizar / confirmar-portal / registrar-falta-portal / remover-falta-portal,
id_convenio=101) e com o consumo de resultados do guias_sync_service (op1-op6).
"""
import json
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Job, UserConvenio
from services.integrador_strategy.base import IntegradorAgendamentoStrategy


class AbaClmfStrategy(IntegradorAgendamentoStrategy):
    id_integrador = 101
    sigla = "ABA_CLMF"

    def _webhook_url(self) -> str:
        return os.getenv("MY_WEBHOOK_URL", "http://localhost:8000/api/jobs/webhook")

    def _user_convenio(self, user_id: int) -> UserConvenio:
        """Credenciais do tenant para o convênio canônico do integrador (101)."""
        return self.db.query(UserConvenio).filter(
            UserConvenio.user_id == user_id,
            UserConvenio.id_convenio == self._id_convenio_canonico()
        ).first()

    def _credenciais_ou_400(self, user_id: int, exigir_login: bool) -> UserConvenio:
        uconv = self._user_convenio(user_id)
        if not exigir_login:
            if not uconv:
                raise HTTPException(
                    status_code=400,
                    detail=f"Usuário não possui credenciais vinculadas para o convênio ID "
                           f"{self._id_convenio_canonico()} (ABA_clmf)."
                )
            return uconv
        if not uconv or not uconv.login or not uconv.senha_criptografada:
            raise HTTPException(status_code=400, detail="Credenciais ABA CLMF não configuradas.")
        return uconv

    # ── OP1 sincronizar_agendamentos ──
    def sincronizar_agendamentos(self, user_id: int, data_inicio=None, data_fim=None,
                                 id_paciente: str = "0") -> Job:
        self._credenciais_ou_400(user_id, exigir_login=False)
        params_dict = {
            "data_inicio": data_inicio.strftime("%Y-%m-%d") if data_inicio else None,
            "data_fim": data_fim.strftime("%Y-%m-%d") if data_fim else None,
            "id_paciente": id_paciente or "0"
        }
        return self._criar_job("sincronizar_agendamentos", user_id, json.dumps(params_dict))

    # ── OP3 confirmar_agendamento ──
    def confirmar_agendamentos(self, user_id: int, portal_ids: list, remover: bool = False) -> Job:
        uconv = self._credenciais_ou_400(user_id, exigir_login=True)
        params_dict = {
            "id_agendamento": portal_ids,
            "num_situacao": 0 if remover else 1,
            "login": uconv.login,
            "senha_criptografada": uconv.senha_criptografada,
            "cod_prestador": uconv.cod_prestador,
            "webhook_url": self._webhook_url()
        }
        return self._criar_job("confirmar_agendamento", user_id, params_dict)

    # ── OP4 registrar_falta ──
    def registrar_falta(self, user_id: int, portal_ids: list, id_paciente: int,
                        motivo_mapeado, doc_justificativa: str = None) -> Job:
        uconv = self._credenciais_ou_400(user_id, exigir_login=True)
        params_dict = {
            "id_agendamento": portal_ids,
            "id_paciente": id_paciente,
            "tipo_desagendamento": motivo_mapeado,
            "doc_justificativa": doc_justificativa or "",
            "login": uconv.login,
            "senha_criptografada": uconv.senha_criptografada,
            "cod_prestador": uconv.cod_prestador,
            "webhook_url": self._webhook_url()
        }
        return self._criar_job("registrar_falta", user_id, params_dict)

    # ── OP5 remover_falta ──
    def remover_falta(self, user_id: int, portal_ids: list, id_paciente: int,
                      data_inicial: str = None, data_final: str = None) -> Job:
        uconv = self._credenciais_ou_400(user_id, exigir_login=True)
        params_dict = {
            "id_agendamento": portal_ids,
            "id_paciente": id_paciente,
            "data_inicial": data_inicial,
            "data_final": data_final,
            "login": uconv.login,
            "senha_criptografada": uconv.senha_criptografada,
            "cod_prestador": uconv.cod_prestador,
            "webhook_url": self._webhook_url()
        }
        return self._criar_job("remover_falta", user_id, params_dict)

    # ── Consumo de resultados (handlers legados do guias_sync_service) ──
    def consumir_resultado(self, job: Job, synced_counts: dict = None) -> None:
        from services.guias_sync_service import _consume_aba_clmf
        _consume_aba_clmf(self.db, job, synced_counts or {"jobs_processed": 0, "inserted": 0,
                                                          "updated": 0, "skipped": 0})
