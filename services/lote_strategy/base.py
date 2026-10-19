"""
LoteStrategy — contrato das estratégias de criação de lote por convênio.

Segue o padrão de services/integrador_strategy e services/solicitacao_strategy:
o hub resolve a estratégia via convenios.id_integrador (fallback id_convenio,
fallback legado IPASGO) e delega a criação do LoteConvenio + job(s).

Tipos de formulário:
  - "numero":  formato IPASGO — usuário informa apenas data_fim; OP13 cria o
               lote no portal e devolve numero_lote.
  - "periodo": formato Unimed — lote definido por data_inicio+data_fim; a OP4
               filtra exatamente esse período; TODOS os itens do filtro são
               vinculados ao lote (mesmo com dataRealizacao anterior ao início).
"""
from abc import ABC, abstractmethod


class LoteStrategy(ABC):

    tipo_formulario = "numero"  # sobrescrito pelas concretas

    @abstractmethod
    def criar_lote(self, db, current_user, request) -> dict:
        """Cria o LoteConvenio e enfileira o(s) job(s) do worker.

        Retorna {"message": str, "id_lote": int}.
        """

    def form_config(self, db, convenio) -> dict:
        """Configuração exibida pelo frontend no formulário de criação."""
        return {
            "id_convenio": getattr(convenio, "id_convenio", None),
            "nome": getattr(convenio, "nome", None),
            "tipo_formulario": self.tipo_formulario,
            "lote_periodo_tipo": getattr(convenio, "lote_periodo_tipo", None),
            "lote_dia_inicio": getattr(convenio, "lote_dia_inicio", None),
            "lote_dia_fim": getattr(convenio, "lote_dia_fim", None),
            "auto_periodo": None,  # preenchido por strategies de período
        }
