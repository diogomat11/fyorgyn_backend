-- Migration: 0013_cancel_stale_pending_jobs.sql
-- Description: Schedule maintenance task to mark stale 'pending' jobs as 'error' after 15 minutes.

-- Schedule: Cancel stale 'pending' jobs
-- Frequency: Every 5 minutes
-- Logic: Jobs in 'pending' state for > 15 minutes are considered abandoned or invalid.
-- Action: Set status to 'error', clear locked_by, and update updated_at.

SELECT cron.schedule(
    'cancel_stale_pending_jobs',
    '*/5 * * * *',
    $$
    UPDATE jobs 
    SET status = 'error', 
        locked_by = NULL,
        updated_at = NOW() 
    WHERE status = 'pending' 
      AND updated_at < NOW() - INTERVAL '15 minutes';
    $$
);
