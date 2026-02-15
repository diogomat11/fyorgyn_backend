-- Migration: 0012_setup_cron_maintenance.sql
-- Description: Enable pg_cron and schedule maintenance tasks (Stale jobs, Old jobs, Old logs)

-- 1. Enable pg_cron extension (Must be run by superuser or allowed in Supabase dashboard)
-- NOTE: If this fails, enable 'pg_cron' in Supabase Dashboard > Database > Extensions
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- 2. Schedule: Mark stale 'processing' jobs as 'error'
-- Frequency: Every 5 minutes
-- Logic: Jobs in 'processing' state for > 15 minutes are considered stuck/crashed.
SELECT cron.schedule(
    'mark_stale_jobs',
    '*/5 * * * *',
    $$
    UPDATE jobs 
    SET status = 'error', 
        updated_at = NOW(),
        locked_by = NULL 
    WHERE status = 'processing' 
      AND updated_at < NOW() - INTERVAL '15 minutes';
    $$
);

-- 3. Schedule: Delete old jobs
-- Frequency: Daily at 03:00 AM
-- Logic: Delete jobs created more than 24 hours ago.
SELECT cron.schedule(
    'delete_old_jobs',
    '0 3 * * *',
    $$
    DELETE FROM jobs 
    WHERE created_at < NOW() - INTERVAL '24 hours';
    $$
);

-- 4. Schedule: Delete old logs
-- Frequency: Daily at 03:30 AM
-- Logic: Delete logs created more than 48 hours ago.
SELECT cron.schedule(
    'delete_old_logs',
    '30 3 * * *',
    $$
    DELETE FROM logs 
    WHERE created_at < NOW() - INTERVAL '48 hours';
    $$
);
