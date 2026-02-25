-- Migration 0020: Execution History
CREATE TABLE IF NOT EXISTS job_executions (
    id SERIAL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE SET NULL,
    rotina TEXT,
    status TEXT, -- success, error, partially_completed
    start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    items_found INTEGER DEFAULT 0,
    error_category TEXT, -- timeout, login_failed, navigation_error, data_parsing
    error_message TEXT,
    meta JSONB, -- Additional stats like browser version, server URL
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for reporting
CREATE INDEX IF NOT EXISTS idx_job_executions_job_id ON job_executions(job_id);
CREATE INDEX IF NOT EXISTS idx_job_executions_convenio ON job_executions(id_convenio);
