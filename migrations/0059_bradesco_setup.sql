-- Migration 0059: Bradesco Setup + Suporte a credenciais dual-portal
-- Bradesco usa portais distintos para autorização (Polimed/Orizon) e faturamento.
-- Cada portal pode ter credenciais diferentes para o mesmo usuário.

-- 1. Registrar convênio Bradesco com id_convenio=1
INSERT INTO convenios (id_convenio, nome)
VALUES (1, 'BRADESCO')
ON CONFLICT (id_convenio) DO NOTHING;

-- 2. Expandir user_convenios para suportar credenciais de portal de faturamento
--    login/senha_criptografada existentes = portal de autorização (padrão)
--    login_fat/senha_fat_criptografada   = portal de faturamento (quando diferente)
ALTER TABLE user_convenios
    ADD COLUMN IF NOT EXISTS login_fat TEXT,
    ADD COLUMN IF NOT EXISTS senha_fat_criptografada TEXT,
    ADD COLUMN IF NOT EXISTS url_portal_fat TEXT;

-- 3. Registrar operações disponíveis do Bradesco
INSERT INTO convenio_operacoes (id_convenio, descricao, valor) VALUES
    (1, 'OP0 - Login Polimed/Orizon', '0'),
    (1, 'OP1 - Solicitar Autorização SADT', '1')
ON CONFLICT DO NOTHING;

-- 4. Regra de prioridade padrão para Bradesco
INSERT INTO priority_rules (id_convenio, rotina, base_priority, escalation_minutes, is_active)
VALUES (1, NULL, 2, 10, TRUE)
ON CONFLICT DO NOTHING;

-- 5. Sync sequence
SELECT setval('convenios_id_convenio_seq', (SELECT MAX(id_convenio) FROM convenios));
