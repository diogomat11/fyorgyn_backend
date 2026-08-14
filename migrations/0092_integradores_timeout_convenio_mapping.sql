-- ============================================================
-- Migration 0092: Integrador Timeout de Captura e Mapeamento Convênio x Integrador
-- ============================================================

-- 1. Adicionar timeout_captura na tabela worker.integradores
ALTER TABLE worker.integradores 
    ADD COLUMN IF NOT EXISTS timeout_captura BOOLEAN DEFAULT FALSE;

-- 2. Ativar timeout de captura (59min) para Unimed Goiânia (id_integrador = 3)
UPDATE worker.integradores 
    SET timeout_captura = TRUE 
    WHERE id_integrador = 3;

-- 3. Adicionar colunas de relacionamento e operações em public.convenios
ALTER TABLE convenios 
    ADD COLUMN IF NOT EXISTS id_integrador INTEGER;

ALTER TABLE convenios 
    ADD COLUMN IF NOT EXISTS operacoes_habilitadas JSONB DEFAULT '[]'::jsonb;

-- 4. Vincular Unimed Intercâmbio (id_convenio = 21) ao integrador Unimed Goiânia (id_integrador = 3)
UPDATE convenios 
    SET id_integrador = 3 
    WHERE id_convenio = 21;

-- 5. Vincular convênios diretos correspondentes
UPDATE convenios 
    SET id_integrador = id_convenio 
    WHERE id_convenio IN (1, 2, 3, 6, 100, 101) AND id_integrador IS NULL;
