-- ============================================================
-- Migration 0096: Configuracoes de convenio - Prazo PEI e Guias Multiprestador
-- ============================================================
-- Description:
--   1. prazo_pei_dias: prazo configuravel de validade do PEI por convenio
--      (substitui hardcoded 180d em pei_service.py e cutoff 270d em
--      3-unimed_goiania/op/op1_consulta.py). O valor e injetado nos params
--      do job pelo job_service (_enrich_params_with_convenio_config).
--      Select do painel: 30,60,90,...,360,365 dias (CHECK 30..365).
--   2. guias_multiprestador: define se o saldo das guias pode ser compartilhado
--      entre diferentes cod_prestador (true) ou se e restrito ao cod_prestador
--      vinculado a guia (false - ex.: IPASGO).
--      Unimed Goiania (id 3) = multiprestador (saldo compartilhado dependendo
--      apenas de relacao carteirinha-prestador).

-- 1. Prazo PEI configuravel (default 180 = valor historico)
ALTER TABLE public.convenios
ADD COLUMN IF NOT EXISTS prazo_pei_dias INTEGER DEFAULT 180
    CHECK (prazo_pei_dias BETWEEN 30 AND 365);

-- 2. Flag de guias multiprestador (default false = restrito ao cod_prestador da guia)
ALTER TABLE public.convenios
ADD COLUMN IF NOT EXISTS guias_multiprestador BOOLEAN DEFAULT false;

-- 3. Seed: Unimed Goiania e multiprestador
UPDATE public.convenios SET guias_multiprestador = TRUE WHERE id_convenio = 3;

-- 4. Comentarios documentacionais
COMMENT ON COLUMN public.convenios.prazo_pei_dias IS 'Prazo em dias da validade do PEI (data_autorizacao + prazo). Afeta tambem o cutoff de paginacao da consulta do scraper. Default historico: 180.';
COMMENT ON COLUMN public.convenios.guias_multiprestador IS 'TRUE: saldo da guia compartilhado entre prestadores com vinculo carteirinha (ex. Unimed Goiania). FALSE: guia restrita ao cod_prestador de origem (ex. IPASGO).';
