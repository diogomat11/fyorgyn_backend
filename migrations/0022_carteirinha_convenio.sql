-- Migration 0022: Carteirinha Convenio Isolation
ALTER TABLE carteirinhas ADD COLUMN IF NOT EXISTS id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL;
