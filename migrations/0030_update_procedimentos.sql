-- Migration: 0030_update_procedimentos
-- Description: Add id_convenio and id_area to procedimentos

-- 1. Add columns to procedimentos if they do not exist
ALTER TABLE procedimentos 
ADD COLUMN IF NOT EXISTS id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS id_area INTEGER REFERENCES areas_atuacao(id_area) ON DELETE SET NULL;
