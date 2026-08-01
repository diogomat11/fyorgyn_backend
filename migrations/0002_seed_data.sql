-- Migration 0002: Seed Data

-- NOTE: Replace the api_key below with your actual key after deployment
INSERT INTO users (username, api_key, status, validade, created_at, updated_at)
VALUES (
    'Clinica Larissa Martins Ferreira',
    'your_api_key_here', -- REPLACE WITH YOUR ACTUAL API KEY
    'Ativo',
    '2026-12-31',
    NOW(),
    NOW()
) ON CONFLICT DO NOTHING;
