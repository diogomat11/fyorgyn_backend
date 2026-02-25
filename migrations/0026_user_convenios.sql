CREATE TABLE IF NOT EXISTS user_convenios (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    UNIQUE(user_id, id_convenio)
);

-- Migrate existing data
INSERT INTO user_convenios (user_id, id_convenio)
SELECT id, id_convenio FROM users WHERE id_convenio IS NOT NULL
ON CONFLICT DO NOTHING;

-- Grant access to Clinica Larissa Martins Ferreira (or any existing test user) for all convenios 
-- so the test can proceed.
INSERT INTO user_convenios (user_id, id_convenio)
SELECT u.id, c.id_convenio 
FROM users u CROSS JOIN convenios c
WHERE u.id_convenio = 3 OR u.username ILIKE '%Larissa%' OR u.username ILIKE '%diogo%'
ON CONFLICT DO NOTHING;
