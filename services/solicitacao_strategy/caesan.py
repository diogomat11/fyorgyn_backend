"""Strategy de solicitação do integrador CAESAN (102) — rotina física op1_autorizacao.

Convênio canônico: 16 (public.convenios 'CAESAN', relação 1:N via id_integrador).
Normaliza os params do form/planilha para as chaves strict da OP1 do 102-caesan.
"""
from fastapi import HTTPException

from .base import SolicitacaoStrategy

# Técnica (planilha modelo_csv) → código de tabela do portal CAESAN
TECNICA_TABELA_MAP = {
    "convencionais": "22",
    "especiais": "00",
}


class CaesanSolicitacaoStrategy(SolicitacaoStrategy):
    id_integrador = 102
    sigla = "CAESAN"
    rotina_fisica = "op1_autorizacao"

    def normalizar_params(self, params: dict) -> dict:
        p = dict(params or {})

        carteira = str(
            p.get("carteira") or p.get("Carteira") or p.get("carteirinha") or ""
        ).strip()
        codigo = str(
            p.get("codigo")
            or p.get("codigoProcedimento_aut")
            or p.get("codigo_procedimento")
            or ""
        ).strip()
        nome_profissional = str(
            p.get("nome_profissional") or p.get("Profissional_nome") or ""
        ).strip()

        if not carteira:
            raise HTTPException(status_code=400, detail="CAESAN: parâmetro obrigatório ausente — carteira.")
        if not codigo:
            raise HTTPException(status_code=400, detail="CAESAN: parâmetro obrigatório ausente — codigo do procedimento.")
        if not nome_profissional:
            raise HTTPException(status_code=400, detail="CAESAN: parâmetro obrigatório ausente — nome_profissional.")

        # Tabela: '22' (Convencionais) | '00' (Especiais); derivável da Técnica da planilha
        tabela = str(p.get("tabela") or "").strip()
        if not tabela:
            tecnica = str(p.get("tecnica") or "").lower()
            for chave, cod in TECNICA_TABELA_MAP.items():
                if chave in tecnica:
                    tabela = cod
                    break
        if tabela not in ("00", "22"):
            raise HTTPException(
                status_code=400,
                detail="CAESAN: parâmetro 'tabela' inválido/ausente. Use '00' (Terapias Especiais), "
                       "'22' (Terapias Convencionais) ou informe a 'tecnica'.",
            )

        try:
            quantidade = int(float(p.get("quantidade") or p.get("qtde") or p.get("qtde_solicitada") or 1))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="CAESAN: parâmetro 'quantidade' inválido.")

        p.update({
            "carteira": carteira,
            "codigo": codigo,
            "nome_profissional": nome_profissional,
            "tabela": tabela,
            "quantidade": quantidade,
            "data_solicitacao": p.get("data_solicitacao") or p.get("dataSolicitacao"),
            "data_atendimento": p.get("data_atendimento") or p.get("data_solicitacao") or p.get("dataSolicitacao"),
            "justificativa": p.get("justificativa") or p.get("texto_Justificativa") or "",
            "caminho_doc": p.get("caminho_doc") or p.get("caminho") or p.get("anexo_RM") or "",
        })
        return p
