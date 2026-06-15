-- ============================================================
-- Migration 0067: Criar Schema Worker
-- Executar no Supabase SQL Editor
-- ============================================================

-- ═══════════════════════════════════════════
-- 1. CRIAR SCHEMA
-- ═══════════════════════════════════════════
CREATE SCHEMA IF NOT EXISTS worker;

-- ═══════════════════════════════════════════
-- 2. TABELAS DO WORKER
-- ═══════════════════════════════════════════

-- Jobs — Fila de jobs com resultado JSON
CREATE TABLE IF NOT EXISTS worker.jobs (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES public.users(id) ON DELETE SET NULL,
    carteirinha_id INT REFERENCES public.carteirinhas(id) ON DELETE CASCADE,
    id_convenio INT REFERENCES public.convenios(id_convenio) ON DELETE SET NULL,
    rotina TEXT,                           -- op0_login, op1_consulta, op2_autorizar, etc.
    params JSONB,                          -- TODOS os parâmetros do job (login, senha, carteirinha, etc.)
    result_data JSONB,                     -- JSON de resposta do worker
    result_consumed BOOLEAN DEFAULT FALSE, -- Flag: backend já consumiu o resultado
    status TEXT NOT NULL DEFAULT 'pending', -- pending, processing, success, error
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 3,
    priority INT DEFAULT 0,
    depending_id INT REFERENCES worker.jobs(id) ON DELETE SET NULL,
    locked_by TEXT,                         -- Server URL que está processando
    error_message TEXT,                     -- Última mensagem de erro
    timeout TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para worker.jobs
CREATE INDEX idx_wjobs_pending ON worker.jobs (status, priority) WHERE status = 'pending';
CREATE INDEX idx_wjobs_user_conv ON worker.jobs (user_id, id_convenio);
CREATE INDEX idx_wjobs_result ON worker.jobs (user_id, status, result_consumed) 
    WHERE status = 'success' AND result_consumed = FALSE;
CREATE INDEX idx_wjobs_locked ON worker.jobs (locked_by) WHERE locked_by IS NOT NULL;
CREATE INDEX idx_wjobs_updated ON worker.jobs (updated_at) WHERE status IN ('processing', 'error');

-- Logs do worker
CREATE TABLE IF NOT EXISTS worker.logs (
    id SERIAL PRIMARY KEY,
    job_id INT REFERENCES worker.jobs(id) ON DELETE SET NULL,
    carteirinha_id INT,
    user_id INT REFERENCES public.users(id) ON DELETE SET NULL,
    level TEXT DEFAULT 'INFO',  -- INFO, WARN, ERROR
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_wlogs_job ON worker.logs (job_id);
CREATE INDEX idx_wlogs_user ON worker.logs (user_id);
CREATE INDEX idx_wlogs_created ON worker.logs USING BRIN (created_at);

-- Workers (heartbeat dos workers)
CREATE TABLE IF NOT EXISTS worker.workers (
    id SERIAL PRIMARY KEY,
    hostname TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'offline',  -- idle, processing, offline, error
    last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
    current_job_id INT,
    command TEXT,                    -- restart, stop
    meta TEXT,                       -- JSON string (CPU, RAM, Version)
    first_error_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Worker Servers (servidores Chrome de cada user/worker)
CREATE TABLE IF NOT EXISTS worker.worker_servers (
    id SERIAL PRIMARY KEY,
    worker_id INT REFERENCES worker.workers(id) ON DELETE CASCADE,
    user_id INT REFERENCES public.users(id) ON DELETE CASCADE,
    server_url TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_ws_worker ON worker.worker_servers (worker_id);
CREATE INDEX idx_ws_user ON worker.worker_servers (user_id);

-- Job Executions (métricas de execução)
CREATE TABLE IF NOT EXISTS worker.job_executions (
    id SERIAL PRIMARY KEY,
    job_id INT REFERENCES worker.jobs(id) ON DELETE CASCADE,
    id_convenio INT,
    rotina TEXT,
    status TEXT,
    start_time TIMESTAMPTZ DEFAULT NOW(),
    end_time TIMESTAMPTZ,
    duration_seconds INT,
    items_found INT DEFAULT 0,
    error_category TEXT,
    error_message TEXT,
    meta JSONB
);
CREATE INDEX idx_wexec_created ON worker.job_executions (start_time DESC);
CREATE INDEX idx_wexec_job ON worker.job_executions (job_id);

-- Server Configs (preferências de afinidade de servidor)
CREATE TABLE IF NOT EXISTS worker.server_configs (
    id SERIAL PRIMARY KEY,
    server_url TEXT UNIQUE NOT NULL,
    id_convenio INT,
    rotina TEXT,
    preference_bonus INT DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Priority Rules (regras de prioridade por convênio/rotina)
CREATE TABLE IF NOT EXISTS worker.priority_rules (
    id SERIAL PRIMARY KEY,
    id_convenio INT,
    rotina TEXT,
    base_priority INT DEFAULT 2,
    escalation_minutes INT DEFAULT 10,
    weight_per_day TEXT,  -- Legacy
    is_active INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════
-- 3. GRANTS
-- ═══════════════════════════════════════════
GRANT USAGE ON SCHEMA worker TO postgres;
GRANT ALL ON ALL TABLES IN SCHEMA worker TO postgres;
GRANT ALL ON ALL SEQUENCES IN SCHEMA worker TO postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA worker GRANT ALL ON TABLES TO postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA worker GRANT ALL ON SEQUENCES TO postgres;

-- Garantir que o service_role também tenha acesso
GRANT USAGE ON SCHEMA worker TO service_role;
GRANT ALL ON ALL TABLES IN SCHEMA worker TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA worker TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA worker GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA worker GRANT ALL ON SEQUENCES TO service_role;

-- ═══════════════════════════════════════════
-- 4. TRIGGER para updated_at automático
-- ═══════════════════════════════════════════
CREATE OR REPLACE FUNCTION worker.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_wjobs_updated BEFORE UPDATE ON worker.jobs
    FOR EACH ROW EXECUTE FUNCTION worker.update_updated_at_column();

CREATE TRIGGER tr_workers_updated BEFORE UPDATE ON worker.workers
    FOR EACH ROW EXECUTE FUNCTION worker.update_updated_at_column();

CREATE TRIGGER tr_ws_updated BEFORE UPDATE ON worker.worker_servers
    FOR EACH ROW EXECUTE FUNCTION worker.update_updated_at_column();

CREATE TRIGGER tr_sc_updated BEFORE UPDATE ON worker.server_configs
    FOR EACH ROW EXECUTE FUNCTION worker.update_updated_at_column();

CREATE TRIGGER tr_pr_updated BEFORE UPDATE ON worker.priority_rules
    FOR EACH ROW EXECUTE FUNCTION worker.update_updated_at_column();
