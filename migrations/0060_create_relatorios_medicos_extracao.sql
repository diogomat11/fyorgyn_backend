-- Migration 0060: Create relatorios_medicos_extracao table
-- Stores therapy extraction data from medical reports via Gemini AI

CREATE TABLE IF NOT EXISTS relatorios_medicos_extracao (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    id_paciente INTEGER NOT NULL,
    nome_paciente TEXT,
    id_relatorio TEXT,
    url_arquivo TEXT,

    -- Cargas horárias por área terapêutica
    carga_psicologia INTEGER,
    carga_fisioterapia INTEGER,
    carga_terapia_ocupacional INTEGER,
    carga_psicopedagogia INTEGER,
    carga_fonoaudiologia INTEGER,
    carga_psicomotricidade INTEGER,
    carga_musicoterapia INTEGER,
    carga_avaliacao_neuropsicologica INTEGER,

    -- Metadados da extração
    tipo_carga_horaria VARCHAR(20),  -- 'semanal' ou 'mensal'
    status_extracao VARCHAR(20) NOT NULL DEFAULT 'NAO_EXTRAIDO',  -- TOTAL, PARCIAL, NAO_EXTRAIDO
    itens_ignorados JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for multi-tenant queries
CREATE INDEX IF NOT EXISTS idx_relatorios_rm_user_id ON relatorios_medicos_extracao(user_id);
CREATE INDEX IF NOT EXISTS idx_relatorios_rm_paciente ON relatorios_medicos_extracao(id_paciente);
CREATE INDEX IF NOT EXISTS idx_relatorios_rm_status ON relatorios_medicos_extracao(status_extracao);
