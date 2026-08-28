-- ============================================================
-- Migration 0106: Cron por (integrador, rotina) + descrição
--
-- Distinção explícita entre agendamentos (2026-08-27): o lote diário desta
-- tabela é de CONSULTA de guias (op1_consulta) para todas as carteirinhas.
-- Execução de sessões (op3_execucao) é job POR AGENDAMENTO com chain
-- automática via convenios/operacoes.modo_execucao e user_convenios.
-- auto_executar — controle na Gestão de Convênios, distinto daqui.
--
-- 1) UNIQUE(id_integrador) -> UNIQUE(id_integrador, rotina): permite
--    agendamentos distintos por integrador no futuro;
-- 2) descricao: rótulo claro no painel admin (aba Agendamentos).
-- ============================================================

ALTER TABLE public.cron_configs ADD COLUMN IF NOT EXISTS descricao TEXT;

ALTER TABLE public.cron_configs DROP CONSTRAINT IF EXISTS cron_configs_id_integrador_key;
ALTER TABLE public.cron_configs DROP CONSTRAINT IF EXISTS uq_cron_configs_integrador;

ALTER TABLE public.cron_configs
    ADD CONSTRAINT uq_cron_configs_integrador_rotina UNIQUE (id_integrador, rotina);

UPDATE public.cron_configs
SET descricao = 'Consultar guias — todas as carteirinhas (lote diário)'
WHERE descricao IS NULL;
