-- ============================================================
-- Migration 0071: Decouple Worker schema from Public schema
-- Drops foreign key constraints from worker tables referencing public tables
-- ============================================================

ALTER TABLE worker.jobs DROP CONSTRAINT IF EXISTS jobs_user_id_fkey;
ALTER TABLE worker.jobs DROP CONSTRAINT IF EXISTS jobs_carteirinha_id_fkey;
ALTER TABLE worker.jobs DROP CONSTRAINT IF EXISTS jobs_id_convenio_fkey;

ALTER TABLE worker.logs DROP CONSTRAINT IF EXISTS logs_user_id_fkey;

ALTER TABLE worker.worker_servers DROP CONSTRAINT IF EXISTS worker_servers_user_id_fkey;
