-- Migration 0018: Add Job multi-convenio fields
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS rotina TEXT;
