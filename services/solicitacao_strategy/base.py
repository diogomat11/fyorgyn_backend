"""Base da família de strategy de solicitação de autorização.

Contrato (espelha integrador_strategy/base.py):
- `rotina_fisica`: rotina do integrador (pertence ao integrador, espaço W);
- `normalizar_params`: valida/normaliza params strict do integrador;
- `montar_job`: monta payload com credenciais do tenant + webhook;
- `criar_job_solicitacao`: envia ao worker (backend_worker) e cria a
  Solicitacao pendente vinculada ao job (padrão IPASGO do POST /jobs/).
"""
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Integrador, Job, Solicitacao, UserConvenio, WorkerIntegrador
from services.job_service import MY_WEBHOOK_URL, _send_jobs_to_worker

ROTINA_CANONICA = "solicitar_autorizacao"


class SolicitacaoStrategy:
    id_integrador = None  # espaço W (worker.integradores)
    sigla = None
    rotina_fisica = None  # rotina do integrador, ex.: 'op1_autorizacao'

    def __init__(self, db: Session):
        self.db = db
        self._uconv_cache = {}

    # ── Identidade (resolvida sempre pela relação, nunca hardcode) ──
    @property
    def nome(self) -> str:
        row = self.db.query(WorkerIntegrador).filter(
            WorkerIntegrador.id_integrador == self.id_integrador
        ).first()
        return row.nome if row else f"Integrador {self.id_integrador}"

    def _id_convenio_canonico(self) -> int:
        row = self.db.query(Integrador.id_convenio).filter(
            Integrador.id_integrador == self.id_integrador
        ).first()
        if not row or not row[0]:
            raise HTTPException(
                status_code=404,
                detail=f"Integrador {self.sigla or self.id_integrador} sem convênio canônico mapeado em public.integradores.",
            )
        return row[0]

    # ── Credenciais do tenant para o convênio canônico ──
    def _user_convenio(self, user_id: int):
        if user_id not in self._uconv_cache:
            self._uconv_cache[user_id] = self.db.query(UserConvenio).filter(
                UserConvenio.user_id == user_id,
                UserConvenio.id_convenio == self._id_convenio_canonico()
            ).first()
        return self._uconv_cache[user_id]

    def _credenciais_ou_400(self, user_id: int) -> UserConvenio:
        uconv = self._user_convenio(user_id)
        if not uconv or not uconv.login or not uconv.senha_criptografada:
            raise HTTPException(
                status_code=400,
                detail=f"Credenciais não cadastradas para o convênio {self.nome}. "
                       "Cadastre as credenciais em Credenciais Portais antes de solicitar autorizações.",
            )
        return uconv

    def _injetar_credenciais(self, p_dict: dict, user_id: int) -> dict:
        uconv = self._credenciais_ou_400(user_id)
        p_dict.setdefault("login", uconv.login)
        p_dict.setdefault("senha_criptografada", uconv.senha_criptografada)

        req_prestador = (
            (p_dict.get("cod_prestador") if isinstance(p_dict.get("cod_prestador"), str) and p_dict.get("cod_prestador").strip() else None)
            or (p_dict.get("codigoPrestador") if isinstance(p_dict.get("codigoPrestador"), str) and p_dict.get("codigoPrestador").strip() else None)
        )
        final_prestador = req_prestador or uconv.cod_prestador or ""
        p_dict["cod_prestador"] = final_prestador
        p_dict["codigoPrestador"] = final_prestador
        p_dict["webhook_url"] = MY_WEBHOOK_URL
        return p_dict

    # ── Montagem e envio ──
    def normalizar_params(self, params: dict) -> dict:
        """Sobrescrever na subclasse: valida e normaliza para as chaves strict do integrador."""
        return dict(params or {})

    def montar_job(self, user_id: int, params: dict, carteirinha_id=None) -> dict:
        p_dict = self.normalizar_params(params)
        p_dict = self._injetar_credenciais(p_dict, user_id)
        return {
            "carteirinha_id": carteirinha_id,
            "id_convenio": self._id_convenio_canonico(),
            "user_id": user_id,
            "rotina": self.rotina_fisica,
            "priority": 0,
            "params": p_dict,
            "max_attempts": 3,
        }

    def criar_job_solicitacao(self, user_id: int, params: dict, carteirinha_id=None) -> dict:
        """Envia 1 job ao worker e cria a Solicitacao pendente vinculada."""
        payload = self.montar_job(user_id, params, carteirinha_id)
        _send_jobs_to_worker([payload], db=self.db)
        self._criar_solicitacoes_pendentes(user_id, payload, count=1)
        return payload

    def _criar_solicitacoes_pendentes(self, user_id: int, payload: dict, count: int) -> None:
        """Cria Solicitacao 'Pendente' por job recém-criado (padrão IPASGO do
        POST /jobs/: guia provisória 'Solicitação #<job_id>', atualizada pelo
        consumer quando o resultado do worker chega)."""
        recent_jobs = self.db.query(Job).filter(
            Job.user_id == user_id,
            Job.id_convenio == payload["id_convenio"],
            Job.rotina == payload["rotina"],
            Job.status == "pending",
        ).order_by(Job.id.desc()).limit(count).all()

        p = payload.get("params") or {}
        for job_row in recent_jobs:
            if self.db.query(Solicitacao).filter(Solicitacao.job_id == job_row.id).first():
                continue
            try:
                qtde = int(float(p.get("quantidade") or p.get("qtde") or 1))
            except (TypeError, ValueError):
                qtde = 1
            self.db.add(Solicitacao(
                user_id=user_id,
                carteirinha_id=job_row.carteirinha_id,
                id_convenio=job_row.id_convenio,
                guia=f"Solicitação #{job_row.id}",
                codigo_terapia=str(p.get("codigo") or p.get("codigo_procedimento") or ""),
                nome_terapia=p.get("nome_terapia") or p.get("especialidade") or "Aguardando autorização...",
                qtde_solicitada=qtde,
                sessoes_autorizadas=0,
                status_solicitacao="Pendente",
                observacao=p.get("justificativa") or p.get("texto_Justificativa"),
                paciente_CID=p.get("cid"),
                job_id=job_row.id,
            ))
        self.db.commit()
