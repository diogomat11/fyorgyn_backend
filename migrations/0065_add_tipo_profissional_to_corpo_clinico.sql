-- Migration 0065: Add tipo_profissional to corpo_clinico table
ALTER TABLE corpo_clinico ADD COLUMN IF NOT EXISTS tipo_profissional TEXT DEFAULT 'profissional';
