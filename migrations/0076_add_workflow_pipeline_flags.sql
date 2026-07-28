-- Migration: 0076_add_workflow_pipeline_flags.sql
-- Description: Adiciona colunas auto_confirmar, auto_executar e auto_faturar para automação em cadeia dos workflows por user_id + convenio

ALTER TABLE public.user_convenios ADD COLUMN IF NOT EXISTS auto_confirmar BOOLEAN DEFAULT FALSE;
ALTER TABLE public.user_convenios ADD COLUMN IF NOT EXISTS auto_executar BOOLEAN DEFAULT FALSE;
ALTER TABLE public.user_convenios ADD COLUMN IF NOT EXISTS auto_faturar BOOLEAN DEFAULT FALSE;
