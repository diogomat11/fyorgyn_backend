-- Migration 0017: Multi-Convenio Support
CREATE TABLE IF NOT EXISTS convenios (
    id_convenio SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    usuario TEXT,
    senha_criptografada TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add id_convenio to base_guias
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL;

-- Seed initial convenios
INSERT INTO convenios (nome) VALUES ('IPASGO'), ('UNIMED'), ('AMIL'), ('SULAMERICA') ON CONFLICT DO NOTHING;
