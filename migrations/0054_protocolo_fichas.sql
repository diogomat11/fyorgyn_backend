-- Tabela de Lotes de Protocolo
CREATE TABLE IF NOT EXISTS protocolo_lotes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_arquivos INTEGER DEFAULT 0,
    total_processado INTEGER DEFAULT 0,
    total_erro INTEGER DEFAULT 0,
    total_sucesso INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_protocolo_lotes_id ON protocolo_lotes(id);
CREATE INDEX IF NOT EXISTS ix_protocolo_lotes_user_id ON protocolo_lotes(user_id);
CREATE INDEX IF NOT EXISTS ix_protocolo_lotes_status ON protocolo_lotes(status);
CREATE INDEX IF NOT EXISTS ix_protocolo_lotes_created_at ON protocolo_lotes(created_at);

-- Tabela de Arquivos de Protocolo
CREATE TABLE IF NOT EXISTS protocolo_arquivos (
    id SERIAL PRIMARY KEY,
    lote_id INTEGER NOT NULL REFERENCES protocolo_lotes(id) ON DELETE CASCADE,
    nome_original TEXT NOT NULL,
    nome_final TEXT,
    status TEXT NOT NULL DEFAULT 'pendente',
    tamanho_bytes INTEGER DEFAULT 0,
    
    -- Extracted data from Gemini
    numero_guia_prestador TEXT,
    nome_beneficiario TEXT,
    numero_guia_principal TEXT,
    atendimentos JSONB,
    
    -- Post-processing data
    guia_normalizada TEXT,
    erro_mensagem TEXT,
    gemini_model_used TEXT,
    gemini_api_key_index INTEGER,
    
    -- Physical file paths
    caminho_original TEXT,
    caminho_final TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_protocolo_arquivos_id ON protocolo_arquivos(id);
CREATE INDEX IF NOT EXISTS ix_protocolo_arquivos_lote_id ON protocolo_arquivos(lote_id);
CREATE INDEX IF NOT EXISTS ix_protocolo_arquivos_status ON protocolo_arquivos(status);
CREATE INDEX IF NOT EXISTS ix_protocolo_arquivos_created_at ON protocolo_arquivos(created_at);
