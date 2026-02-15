-- Migration: 0014_create_workers_table.sql
-- Description: Create workers table for health monitoring

CREATE TABLE IF NOT EXISTS workers (
    id SERIAL PRIMARY KEY,
    hostname TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'offline',
    last_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    current_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    command TEXT,
    meta TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_workers_hostname ON workers(hostname);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_last_heartbeat ON workers(last_heartbeat);
