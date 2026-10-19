-- 0112 — Configuração de período do lote por convênio (plano lote_strategy)
-- lote_periodo_tipo: 'mensal_fixo' (janela fixa dia_inicio→dia_fim, auto-preenchida
--                    no formulário) | 'livre' (usuário escolhe) | NULL (strategy
--                    legado — formato número/IPASGO).
-- Seeds (decisão 2026-10-18):
--   conv 3  (UNIMED GOIANIA)      = mensal_fixo, 18 → 17
--   conv 21 (UNIMED INTERCAMBIO)  = mensal_fixo, 18 → 17
--   conv 6  (IPASGO)              = livre (formato atual preservado: apenas data_fim)

ALTER TABLE public.convenios ADD COLUMN IF NOT EXISTS lote_periodo_tipo TEXT NULL;
ALTER TABLE public.convenios ADD COLUMN IF NOT EXISTS lote_dia_inicio INT NULL;
ALTER TABLE public.convenios ADD COLUMN IF NOT EXISTS lote_dia_fim INT NULL;

UPDATE public.convenios SET lote_periodo_tipo = 'mensal_fixo', lote_dia_inicio = 18, lote_dia_fim = 17
WHERE id_convenio IN (3, 21) AND lote_periodo_tipo IS NULL;

UPDATE public.convenios SET lote_periodo_tipo = 'livre'
WHERE id_convenio = 6 AND lote_periodo_tipo IS NULL;
