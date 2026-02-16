-- Migration: 0016_auto_restart_stale_workers.sql
-- Description: Schedule a cron job to flag workers as 'restart' if they are stuck (idle/processing) but silent for > 1 minute.

SELECT cron.schedule(
    'watchdog_stale_workers',
    '* * * * *', -- Run every minute
    $$
    UPDATE workers 
    SET command = 'restart' 
    WHERE status IN ('idle', 'processing') 
    AND last_heartbeat < (NOW() - INTERVAL '1 minute')
    AND (command IS NULL OR command != 'restart');
    $$
);
