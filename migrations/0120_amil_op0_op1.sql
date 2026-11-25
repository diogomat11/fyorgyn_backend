-- 0120 — AMIL (convênio 9): OP0 Login + OP1 Consulta Guias
--
-- Plano: docs/plano_amil_op0_op1.md (decisão D2 revisada: único campo da API
-- AMIL sem equivalente em base_guias é o pedidoConfirmado; o pacote de
-- faturamento NÃO vira coluna — o vínculo codigo_procedimento → faturamento é
-- cadastrado pela sincronização na tabela de códigos existente
-- public.procedimentos (coluna "faturamento"), sem DDL novo. Decisão D3:
-- migration é APENAS DDL — cadastro de OPs e credenciais fica nos seeds
-- scripts/seed_amil_ops.py e scripts/seed_amil.py, nunca em migration).
--
-- Coluna nova em public.base_guias:
--   status_token <- pedidoConfirmado (detalhe da guia na API AMIL)

ALTER TABLE public.base_guias ADD COLUMN IF NOT EXISTS status_token BOOLEAN NULL;

COMMENT ON COLUMN public.base_guias.status_token IS 'AMIL (convênio 9): pedidoConfirmado da API de detalhe da guia — true quando o pedido de autorização foi confirmado pelo prestador';
