-- Migration 0069: Create protocolo_itens table and update protocolo_arquivos

-- Add carteira and gravado to protocolo_arquivos
ALTER TABLE protocolo_arquivos ADD COLUMN IF NOT EXISTS carteira TEXT;
ALTER TABLE protocolo_arquivos ADD COLUMN IF NOT EXISTS gravado BOOLEAN DEFAULT FALSE;

-- Create table protocolo_itens
CREATE TABLE IF NOT EXISTS protocolo_itens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    cod_prestador TEXT,
    guia TEXT,
    nome TEXT,
    carteira TEXT,
    senha TEXT,
    data DATE,
    assinatura TEXT,
    guia_prestador TEXT,
    lote_id INTEGER REFERENCES protocolo_lotes(id) ON DELETE CASCADE,
    arquivo_id INTEGER REFERENCES protocolo_arquivos(id) ON DELETE CASCADE,
    base_guia_id INTEGER REFERENCES base_guias(id) ON DELETE SET NULL,
    caminho_arquivo TEXT,
    faturamento_lote_id INTEGER REFERENCES faturamento_lotes(id) ON DELETE SET NULL,
    agendamento_id INTEGER REFERENCES agendamentos(id_agendamento) ON DELETE SET NULL,
    status_conciliacao TEXT DEFAULT 'Não Conciliado' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_user_id ON protocolo_itens(user_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_id_convenio ON protocolo_itens(id_convenio);
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_lote_id ON protocolo_itens(lote_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_arquivo_id ON protocolo_itens(arquivo_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_base_guia_id ON protocolo_itens(base_guia_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_status_conciliacao ON protocolo_itens(status_conciliacao);
CREATE INDEX IF NOT EXISTS idx_protocolo_itens_data ON protocolo_itens(data);
