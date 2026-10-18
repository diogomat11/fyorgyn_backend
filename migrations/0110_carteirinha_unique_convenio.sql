-- ============================================================
-- Migration 0110: Unicidade de carteirinha passa a considerar o convênio
--
-- Antes (0063):  UNIQUE (carteirinha, user_id)  → uq_carteirinha_user_id
-- Depois:        UNIQUE INDEX (carteirinha, COALESCE(id_convenio,0), user_id)
--                → uq_carteirinha_conv_user
--
-- Motivação: o mesmo número de carteirinha pode existir em convênios
-- distintos para o mesmo tenant (ex. CAESAN 16 vs convênios criados pela
-- importação Evoluir) — sem relação entre si, são itens distintos.
-- COALESCE(id_convenio,0) trata linhas legadas sem convênio como 0,
-- preservando a semântica anterior (mesmo estilo da uq_guia_conv_ter_cart,
-- migration 0066). Nenhum dado existente viola a nova chave: todo par
-- (carteirinha, user_id) duplicado já seria rejeitado pela constraint antiga.
-- ============================================================

ALTER TABLE public.carteirinhas
    DROP CONSTRAINT IF EXISTS uq_carteirinha_user_id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_carteirinha_conv_user
    ON public.carteirinhas (carteirinha, COALESCE(id_convenio, 0), user_id);
