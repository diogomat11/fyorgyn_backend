-- Migration 0037: Job Orchestrator Enhancements
-- 1. Add escalation_minutes to priority_rules
ALTER TABLE priority_rules ADD COLUMN IF NOT EXISTS escalation_minutes INTEGER DEFAULT 10;

-- 2. Update comment on weight_per_day (kept for backward compat, superseded by escalation_minutes)
COMMENT ON COLUMN priority_rules.escalation_minutes IS 
  'Minutes between each priority step-up. e.g. base_priority=2, escalation_minutes=10: job becomes priority 1 after 10min, priority 0 after 20min (top of queue).';

-- 3. Create server_configs table for soft server-preference rules
CREATE TABLE IF NOT EXISTS server_configs (
    id SERIAL PRIMARY KEY,
    server_url TEXT NOT NULL UNIQUE,        -- e.g. "http://127.0.0.1:9000"
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL,
    rotina TEXT,                             -- NULL = any rotina for preferred convenio
    preference_bonus INTEGER DEFAULT 1,      -- points subtracted from effective_priority when this server handles a matching job (lower = higher priority)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast lookups in dispatcher
CREATE INDEX IF NOT EXISTS idx_server_configs_url ON server_configs(server_url);
CREATE INDEX IF NOT EXISTS idx_server_configs_convenio ON server_configs(id_convenio);

COMMENT ON TABLE server_configs IS
  'Soft-preference rules for worker servers. The dispatcher gives a bonus to a server when it receives a job matching its preferred convenio/rotina, maximising session reuse without hard-binding.';
