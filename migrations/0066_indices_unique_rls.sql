-- ============================================================
-- Migration 0066: Índices de Performance + UNIQUE Constraint
-- Executar no Supabase SQL Editor
-- ============================================================

-- ═══════════════════════════════════════════
-- 1. ÍNDICES PARA TABELAS EXISTENTES (public)
-- ═══════════════════════════════════════════

-- Jobs: dispatcher SELECT WHERE status='pending' ORDER BY priority (a cada 15s)
CREATE INDEX IF NOT EXISTS idx_jobs_pending 
    ON jobs (status, priority) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_jobs_user_convenio 
    ON jobs (user_id, id_convenio);

CREATE INDEX IF NOT EXISTS idx_jobs_locked 
    ON jobs (locked_by) WHERE locked_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_updated_status 
    ON jobs (updated_at) WHERE status IN ('processing', 'error');

-- Base Guias: upsert + listagem frontend
CREATE INDEX IF NOT EXISTS idx_guias_cart_conv 
    ON base_guias (carteirinha_id, id_convenio);

CREATE INDEX IF NOT EXISTS idx_guias_guia_conv_ter 
    ON base_guias (guia, id_convenio, codigo_terapia);

CREATE INDEX IF NOT EXISTS idx_guias_cod_benef 
    ON base_guias (codigo_beneficiario) WHERE codigo_beneficiario IS NOT NULL;

-- Logs: busca por job
CREATE INDEX IF NOT EXISTS idx_logs_job 
    ON logs (job_id);

-- Carteirinhas: busca multitenant
CREATE INDEX IF NOT EXISTS idx_cart_user_conv 
    ON carteirinhas (user_id, id_convenio);

-- User Convenios: lookup de credenciais
CREATE INDEX IF NOT EXISTS idx_uconv_user_conv 
    ON user_convenios (user_id, id_convenio);

-- ═══════════════════════════════════════════
-- 2. UNIQUE CONSTRAINT para INSERT ON CONFLICT
-- ═══════════════════════════════════════════

-- Verificar se o índice funcional antigo (0047) existe e removê-lo
-- O antigo não inclui carteirinha_id nem user_id (não é multi-tenant safe)
DROP INDEX IF EXISTS uix_base_guias_guia_terapia_convenio;

-- Novo constraint multi-tenant aware
-- Trata NULLs com COALESCE para garantir unicidade mesmo com valores nulos
CREATE UNIQUE INDEX IF NOT EXISTS uq_guia_conv_ter_cart
    ON base_guias (
        guia, 
        COALESCE(id_convenio, 0), 
        COALESCE(codigo_terapia, ''), 
        COALESCE(carteirinha_id, 0)
    );

-- ═══════════════════════════════════════════
-- 3. RLS (Row Level Security) — Defense in Depth
-- ═══════════════════════════════════════════

-- Ativar RLS nas tabelas principais
ALTER TABLE base_guias ENABLE ROW LEVEL SECURITY;
ALTER TABLE carteirinhas ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agendamentos ENABLE ROW LEVEL SECURITY;
ALTER TABLE faturamento_lotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE lotes_convenio ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_pei ENABLE ROW LEVEL SECURITY;
ALTER TABLE corpo_clinico ENABLE ROW LEVEL SECURITY;

-- NOTA: service_role bypassa RLS automaticamente.
-- As policies abaixo são segurança extra caso roles com menos privilégio sejam usados.

-- Policies permissivas para service_role (nosso backend usa service_role)
-- Estas garantem que o backend (via service_role) funcione sem restrições
CREATE POLICY "service_role_full_access" ON base_guias 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON carteirinhas 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON jobs 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON logs 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON agendamentos 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON faturamento_lotes 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON lotes_convenio 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON patient_pei 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
CREATE POLICY "service_role_full_access" ON corpo_clinico 
    FOR ALL TO postgres USING (true) WITH CHECK (true);
