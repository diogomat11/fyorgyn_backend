-- Migration: 0075_add_modo_execucao_workflow.sql
-- Description: Adiciona coluna modo_execucao (automatico/manual) em convenios e worker.convenio_operacoes

ALTER TABLE public.convenios ADD COLUMN IF NOT EXISTS modo_execucao TEXT DEFAULT 'automatico';
ALTER TABLE worker.convenio_operacoes ADD COLUMN IF NOT EXISTS modo_execucao TEXT DEFAULT 'automatico';
