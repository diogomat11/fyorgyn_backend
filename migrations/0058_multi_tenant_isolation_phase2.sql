-- Migration 0058: Multi-Tenant Phase 2 - Additional Isolation and Cleanup

-- 1. Add user_id to remaining data tables
ALTER TABLE base_guias
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE agendamentos
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE faturamento_lotes
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE protocolo_lotes
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE corpo_clinico
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE logs
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;



ALTER TABLE patient_pei
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- 2. Migrate legacy records to the first user (default admin)
DO $$
DECLARE
    admin_user_id INTEGER;
BEGIN
    SELECT id INTO admin_user_id FROM users ORDER BY id ASC LIMIT 1;

    IF admin_user_id IS NOT NULL THEN
        UPDATE base_guias SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE agendamentos SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE faturamento_lotes SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE protocolo_lotes SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE corpo_clinico SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE logs SET user_id = admin_user_id WHERE user_id IS NULL;
        UPDATE patient_pei SET user_id = admin_user_id WHERE user_id IS NULL;
        
        -- As defined in Phase 1: Set Unimed Anapolis records (id_convenio=2) to user_id=15 (if such user exists)
        IF EXISTS (SELECT 1 FROM users WHERE id = 15) THEN
            UPDATE base_guias SET user_id = 15 WHERE id_convenio = 2;
            UPDATE carteirinhas SET user_id = 15 WHERE id_convenio = 2;
            UPDATE jobs SET user_id = 15 WHERE id_convenio = 2;
        END IF;

        RAISE NOTICE 'Additional legacy tables migrated with success';
    END IF;
END $$;

-- 3. Performance indexes for user_id filters
CREATE INDEX IF NOT EXISTS idx_base_guias_user_id ON base_guias(user_id);
CREATE INDEX IF NOT EXISTS idx_agendamentos_user_id ON agendamentos(user_id);
CREATE INDEX IF NOT EXISTS idx_faturamento_lotes_user_id ON faturamento_lotes(user_id);
CREATE INDEX IF NOT EXISTS idx_protocolo_lotes_user_id ON protocolo_lotes(user_id);
CREATE INDEX IF NOT EXISTS idx_corpo_clinico_user_id ON corpo_clinico(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id);
CREATE INDEX IF NOT EXISTS idx_patient_pei_user_id ON patient_pei(user_id);

-- 4. Remove legacy credentials from Convenios (Phase 5)
ALTER TABLE convenios
    DROP COLUMN IF EXISTS usuario,
    DROP COLUMN IF EXISTS senha_criptografada,
    DROP COLUMN IF EXISTS codigo_referenciado;
