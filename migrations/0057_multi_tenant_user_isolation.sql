-- Migration 0057: Multi-Tenant - Credenciais por usuário e isolamento de dados

-- 1. Adicionar credenciais e cod_prestador na tabela de vínculo user_convenios
ALTER TABLE user_convenios
    ADD COLUMN IF NOT EXISTS login TEXT,
    ADD COLUMN IF NOT EXISTS senha_criptografada TEXT,
    ADD COLUMN IF NOT EXISTS cod_prestador TEXT;

-- 2. Adicionar is_admin ao usuário para controle de visibilidade futura
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- 3. Adicionar user_id nas tabelas de dados (com nullable para registros legado)
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE lotes_convenio
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE lotes_agendamento
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE carteirinhas
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- 4. Migrar registros legado: vincular tudo ao usuário com menor ID (o primeiro usuário/admin)
DO $$
DECLARE
    admin_user_id INTEGER;
BEGIN
    SELECT id INTO admin_user_id FROM users ORDER BY id ASC LIMIT 1;

    IF admin_user_id IS NOT NULL THEN
        UPDATE jobs SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE lotes_convenio SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE lotes_agendamento SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE carteirinhas SET user_id = admin_user_id WHERE user_id IS NULL;

        -- Marcar o primeiro usuário como admin
        UPDATE users SET is_admin = TRUE WHERE id = admin_user_id;

        RAISE NOTICE 'Legado migrado com sucesso para user_id = %', admin_user_id;
    END IF;
END $$;

-- 5. Índices de performance para os filtros por user
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_lotes_convenio_user_id ON lotes_convenio(user_id);
CREATE INDEX IF NOT EXISTS idx_lotes_agendamento_user_id ON lotes_agendamento(user_id);
CREATE INDEX IF NOT EXISTS idx_carteirinhas_user_id ON carteirinhas(user_id);
