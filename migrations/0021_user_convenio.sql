-- Migration 0021: User Convenio Isolation
ALTER TABLE users ADD COLUMN IF NOT EXISTS id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL;
