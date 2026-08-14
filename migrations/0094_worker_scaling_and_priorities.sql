-- ============================================================
-- Migration 0094: Integrar Scaling e Regras de Prioridade no Worker
-- ============================================================

ALTER TABLE worker.worker_api_keys 
ADD COLUMN IF NOT EXISTS max_servers INT DEFAULT 1,
ADD COLUMN IF NOT EXISTS dispatch_stagger_seconds INT DEFAULT 15,
ADD COLUMN IF NOT EXISTS id_convenio_preferencial INT NULL,
ADD COLUMN IF NOT EXISTS rotina_preferencial TEXT NULL,
ADD COLUMN IF NOT EXISTS preference_bonus INT DEFAULT 1,
ADD COLUMN IF NOT EXISTS base_priority INT DEFAULT 2,
ADD COLUMN IF NOT EXISTS escalation_minutes INT DEFAULT 10;
