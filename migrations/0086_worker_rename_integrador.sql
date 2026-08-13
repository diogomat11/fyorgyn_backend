-- Migration 0086: Renomear worker.convenios -> worker.integradores com preservação de dados

-- 1. Renomear tabelas no schema worker
ALTER TABLE IF EXISTS worker.convenios RENAME TO integradores;
ALTER TABLE IF EXISTS worker.convenio_operacoes RENAME TO integrador_operacoes;

-- 2. Renomear colunas id_convenio -> id_integrador nas tabelas renomeadas
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'worker' AND table_name = 'integradores' AND column_name = 'id_convenio'
    ) THEN
        ALTER TABLE worker.integradores RENAME COLUMN id_convenio TO id_integrador;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'worker' AND table_name = 'integrador_operacoes' AND column_name = 'id_convenio'
    ) THEN
        ALTER TABLE worker.integrador_operacoes RENAME COLUMN id_convenio TO id_integrador;
    END IF;
END $$;

-- 3. Recriar Foreign Key
ALTER TABLE worker.integrador_operacoes
    DROP CONSTRAINT IF EXISTS convenio_operacoes_id_convenio_fkey;

ALTER TABLE worker.integrador_operacoes
    DROP CONSTRAINT IF EXISTS integrador_operacoes_id_integrador_fkey;

ALTER TABLE worker.integrador_operacoes
    ADD CONSTRAINT integrador_operacoes_id_integrador_fkey
    FOREIGN KEY (id_integrador) REFERENCES worker.integradores(id_integrador)
    ON DELETE CASCADE;

-- 4. Views de retrocompatibilidade
CREATE OR REPLACE VIEW worker.convenios AS
    SELECT id_integrador AS id_convenio, nome, sigla, ativo, created_at, updated_at
    FROM worker.integradores;

CREATE OR REPLACE VIEW worker.convenio_operacoes AS
    SELECT id, id_integrador AS id_convenio, rotina, descricao, ativo, params_schema, created_at
    FROM worker.integrador_operacoes;

-- 5. Adicionar coluna tipo_operacao na worker.integradores
ALTER TABLE worker.integradores
    ADD COLUMN IF NOT EXISTS tipo_operacao TEXT DEFAULT 'convenio'
    CHECK (tipo_operacao IN ('convenio', 'agendamento'));

UPDATE worker.integradores SET tipo_operacao = 'agendamento'
    WHERE id_integrador IN (100, 101);

-- 6. Adicionar coluna tipo_processamento na worker.integrador_operacoes
ALTER TABLE worker.integrador_operacoes
    ADD COLUMN IF NOT EXISTS tipo_processamento TEXT DEFAULT 'local'
    CHECK (tipo_processamento IN ('local', 'server', 'remoto'));

-- 7. Tabela de configuração global de workers
CREATE TABLE IF NOT EXISTS worker.worker_config (
    id SERIAL PRIMARY KEY,
    chave TEXT UNIQUE NOT NULL,
    valor TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO worker.worker_config (chave, valor) VALUES
    ('max_servers', '7'),
    ('dispatch_stagger_seconds', '15')
ON CONFLICT (chave) DO NOTHING;

-- 8. Tabela de API Keys por Worker (Multi-tenancy)
CREATE TABLE IF NOT EXISTS worker.worker_api_keys (
    id SERIAL PRIMARY KEY,
    api_key TEXT UNIQUE NOT NULL,
    user_id INT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    tipo_processamento TEXT NOT NULL DEFAULT 'local'
        CHECK (tipo_processamento IN ('local', 'server', 'remoto')),
    descricao TEXT,
    ativo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
