-- ============================================================
-- Migration 0098: Cron Configs - agendamento diario de jobs por integrador
-- ============================================================
-- Description:
--   Configuracao de cron diario (adaptado do clmf_hub_basic/backend/main.py)
--   para criar jobs de consulta (op1_consulta) de TODAS as carteirinhas
--   ativas dos convenios de um integrador, com painel admin para
--   habilitar/desabilitar e definir horario.
--
--   Diferencas vs clmf_hub_basic:
--   - NAO importa a etapa de limpeza (DELETE de pei_temp/patient_pei/base_guias)
--     - base_guias aqui e multi-tenant e a limpeza completa e perigosa.
--   - Guarda de execucao (ultimo_run) fica NO BANCO, nao em memoria
--     (idempotente apos restart do backend; o clmf usava globals em memoria).
--   - Catch-up: se o backend estava fora do ar no horario agendado, executa
--     assim que subir (agora >= horario E ultimo_run < hoje).
--   - Timezone explicita por config (servidor Render roda em UTC).

CREATE TABLE IF NOT EXISTS public.cron_configs (
    id SERIAL PRIMARY KEY,
    id_integrador INTEGER NOT NULL UNIQUE,  -- ref. logica a worker.integradores (sem FK cross-schema, padrao migration 0071)
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    horario TIME NOT NULL DEFAULT '23:01',
    fuso TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    rotina TEXT NOT NULL DEFAULT 'op1_consulta',
    ultimo_run DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cron_configs_enabled ON public.cron_configs(enabled) WHERE enabled;

-- Seed: integrador 3 (Unimed Goiania) - replica o comportamento do clmf (23:01)
INSERT INTO public.cron_configs (id_integrador, enabled, horario, fuso, rotina)
VALUES (3, TRUE, '23:01', 'America/Sao_Paulo', 'op1_consulta')
ON CONFLICT (id_integrador) DO NOTHING;

COMMENT ON TABLE public.cron_configs IS 'Cron diario por integrador: cria jobs (rotina, default op1_consulta) para todas as carteirinhas ativas dos convenios do integrador. ultimo_run no banco garante idempotencia pos-restart.';
COMMENT ON COLUMN public.cron_configs.id_integrador IS 'Referencia logica a worker.integradores.id_integrador (FK cross-schema propositalmente ausente - ver migration 0071).';
