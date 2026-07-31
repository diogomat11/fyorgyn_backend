-- Migration 0083: corpo_clinico - id SERIAL PK + colunas CRM + UNIQUE areas_atuacao.nome
-- Description: Prepara a tabela corpo_clinico para receber dados da API de consulta/importacao
--              CRM medico (CFM nacional). Adiciona PK SERIAL (id), colunas situacao e
--              atualizado_crm, e UNIQUE em areas_atuacao.nome para upsert de especialidades.
--
-- Contexto:
--   - PK anterior era composta (id_profissional, area) - id_profissional tem valores
--     mistos (UUIDs, numericos, NULL) e nao e confiavel como identificador unico.
--   - Medicos importados via CRM terao id_profissional = NULL (vazio) - o novo id SERIAL
--     passa a ser o identificador estavel.
--   - Nenhuma FK referencia corpo_clinico (confirmado via pg_constraint), entao migrar
--     a PK nao quebra dependencias.
--
-- Referencias:
--   - Plano: Prompts_implantacoes/Contextos/2026_07_29_Plano_API_Consulta_CRM_Medico.md (RF-F3)

-- 1. Adicionar coluna id SERIAL (nao PK ainda - popular primeiro)
ALTER TABLE public.corpo_clinico ADD COLUMN IF NOT EXISTS id SERIAL;

-- 2. Dropar PK antiga composta (id_profissional, area)
ALTER TABLE public.corpo_clinico DROP CONSTRAINT IF EXISTS corpo_clinico_pkey;

-- 3. Promover id a PRIMARY KEY
ALTER TABLE public.corpo_clinico ADD CONSTRAINT corpo_clinico_pkey PRIMARY KEY (id);

-- 4. Manter (id_profissional, area) como UNIQUE (preserva queries/semantica existente)
--    Postgres trata NULLs em UNIQUE como distintos - medicos importados (id_profissional=NULL)
--    nao conflitam entre si.
ALTER TABLE public.corpo_clinico ADD CONSTRAINT corpo_clinico_id_prof_area_key
    UNIQUE (id_profissional, area);

-- 5. Adicionar situacao (status no CRM: ativo/inativo/cancelado - gravado em lowercase)
ALTER TABLE public.corpo_clinico ADD COLUMN IF NOT EXISTS situacao TEXT;

-- 6. Adicionar atualizado_crm (timestamp da ultima consulta ao CFM)
ALTER TABLE public.corpo_clinico ADD COLUMN IF NOT EXISTS atualizado_crm TIMESTAMPTZ;

-- 7. UNIQUE em areas_atuacao.nome (necessario para upsert de especialidades via ON CONFLICT)
--    Verifica se ja existe antes de criar (idempotente).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'areas_atuacao'
          AND indexdef ILIKE '%UNIQUE%nome%'
    ) THEN
        -- Antes de criar UNIQUE, normalizar duplicatas existentes em nome (se houver)
        -- anexando sufico - caso contrario o CREATE UNIQUE INDEX falharia.
        -- Seguranca: so executa se houver duplicatas; usa row_number para manter a 1a.
        DELETE FROM public.areas_atuacao a
        WHERE id_area IN (
            SELECT id_area FROM (
                SELECT id_area, ROW_NUMBER() OVER (PARTITION BY lower(nome) ORDER BY id_area) AS rn
                FROM public.areas_atuacao
                WHERE nome IS NOT NULL
            ) z WHERE z.rn > 1
        );
        CREATE UNIQUE INDEX areas_atuacao_nome_key ON public.areas_atuacao (nome);
    END IF;
END $$;

-- 8. Comentarios documentacionais
COMMENT ON COLUMN public.corpo_clinico.id IS 'PK SERIAL - identificador estavel (medicos importados via CRM tem id_profissional=NULL)';
COMMENT ON COLUMN public.corpo_clinico.situacao IS 'Situacao no CRM (ativo/inativo/cancelado) em lowercase. Distinto de status (ativo/inativo do profissional no sistema).';
COMMENT ON COLUMN public.corpo_clinico.atualizado_crm IS 'Timestamp da ultima consulta de dados ao portal CFM (corresponde ao atualizado_em do JSON do scraper).';
