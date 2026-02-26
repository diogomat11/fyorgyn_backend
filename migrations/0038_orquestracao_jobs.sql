-- Migration 0038: Job Orchestration, Workflow execution dependencies and Flags

-- 1. Table `base_guias`
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS timestamp_captura TIMESTAMP WITH TIME ZONE;

-- 2. Table `jobs`
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS depending_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;

-- 3. Table `agendamentos`
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS execucao_status TEXT DEFAULT 'pendente';

-- 4. Table `convenios`
ALTER TABLE convenios ADD COLUMN IF NOT EXISTS biometria BOOLEAN DEFAULT FALSE;
ALTER TABLE convenios ADD COLUMN IF NOT EXISTS timeout_captura BOOLEAN DEFAULT FALSE;
ALTER TABLE convenios ADD COLUMN IF NOT EXISTS pei_automatico BOOLEAN DEFAULT FALSE;

