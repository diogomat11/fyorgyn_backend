-- Migration 0019: Priority Rules
CREATE TABLE IF NOT EXISTS priority_rules (
    id SERIAL PRIMARY KEY,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    rotina TEXT,
    base_priority INTEGER DEFAULT 1,
    weight_per_day FLOAT DEFAULT 0.1, -- Bonus priority per day waiting
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed some initial rules
INSERT INTO priority_rules (id_convenio, rotina, base_priority, weight_per_day)
VALUES 
(1, 'op3_import_guias', 1, 0.5), -- IPASGO imports are important
(2, 'consulta_guias', 1, 0.2)   -- Unimed regular queries
ON CONFLICT DO NOTHING;
