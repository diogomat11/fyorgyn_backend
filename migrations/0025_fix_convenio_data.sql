-- Migration 0025: Robust ID Alignment and Uniqueness
-- IPASGO=6, UNIMED GOIANIA=3, UNIMED ANAPOLIS=2, SULAMERICA=8, AMIL=9

DO $$
BEGIN
    -- 1. Ensure Target IDs exist (temporarily as 'NEW_...')
    INSERT INTO convenios (id_convenio, nome) VALUES (102, 'NEW_UNIMED_ANAPOLIS') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (103, 'NEW_UNIMED_GOIANIA') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (106, 'NEW_IPASGO') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (108, 'NEW_SULAMERICA') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (109, 'NEW_AMIL') ON CONFLICT DO NOTHING;

    -- 2. Update child tables from OLD IDs to NEW IDs
    -- IPASGO (curr 1) -> 106
    -- UNIMED GOIANIA (curr 2) -> 103
    -- AMIL (curr 3) -> 109
    -- SULAMERICA (curr 4) -> 108
    -- UNIMED (ANAPOLIS / ID 30? No, image showed IDs up to 36, I cleaned > 20)
    -- If there's no Anapolis yet, it stays as is.

    -- Update refs
    UPDATE users SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE users SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE users SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE users SET id_convenio = 108 WHERE id_convenio = 4;

    UPDATE carteirinhas SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE carteirinhas SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE carteirinhas SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE carteirinhas SET id_convenio = 108 WHERE id_convenio = 4;

    UPDATE jobs SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE jobs SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE jobs SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE jobs SET id_convenio = 108 WHERE id_convenio = 4;

    UPDATE base_guias SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE base_guias SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE base_guias SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE base_guias SET id_convenio = 108 WHERE id_convenio = 4;

    UPDATE priority_rules SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE priority_rules SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE priority_rules SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE priority_rules SET id_convenio = 108 WHERE id_convenio = 4;
    
    -- Cleanup other tables
    UPDATE job_executions SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE job_executions SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE job_executions SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE job_executions SET id_convenio = 108 WHERE id_convenio = 4;

    UPDATE fichas SET id_convenio = 106 WHERE id_convenio = 1;
    UPDATE fichas SET id_convenio = 103 WHERE id_convenio = 2;
    UPDATE fichas SET id_convenio = 109 WHERE id_convenio = 3;
    UPDATE fichas SET id_convenio = 108 WHERE id_convenio = 4;

    -- 3. Consolidate into Convenios Table
    -- Save Credentials
    CREATE TEMP TABLE tmp_creds AS SELECT * FROM convenios WHERE id_convenio IN (1,2,3,4);
    
    DELETE FROM convenios WHERE id_convenio IN (1,2,3,4);

    -- Move from New IDs back to Desired IDs (without collision now)
    -- We'll use the target final IDs requested: 2, 3, 6, 8, 9
    INSERT INTO convenios (id_convenio, nome) VALUES (2, 'UNIMED ANAPOLIS') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (3, 'UNIMED GOIANIA') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (6, 'IPASGO') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (8, 'SULAMERICA') ON CONFLICT DO NOTHING;
    INSERT INTO convenios (id_convenio, nome) VALUES (9, 'AMIL') ON CONFLICT DO NOTHING;

    -- Final update of child tables to target IDs
    UPDATE users SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE users SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE users SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE users SET id_convenio = 9 WHERE id_convenio = 109;

    UPDATE carteirinhas SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE carteirinhas SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE carteirinhas SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE carteirinhas SET id_convenio = 9 WHERE id_convenio = 109;

    UPDATE jobs SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE jobs SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE jobs SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE jobs SET id_convenio = 9 WHERE id_convenio = 109;

    UPDATE base_guias SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE base_guias SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE base_guias SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE base_guias SET id_convenio = 9 WHERE id_convenio = 109;

    UPDATE priority_rules SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE priority_rules SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE priority_rules SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE priority_rules SET id_convenio = 9 WHERE id_convenio = 109;
    
    UPDATE job_executions SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE job_executions SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE job_executions SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE job_executions SET id_convenio = 9 WHERE id_convenio = 109;

    UPDATE fichas SET id_convenio = 6 WHERE id_convenio = 106;
    UPDATE fichas SET id_convenio = 3 WHERE id_convenio = 103;
    UPDATE fichas SET id_convenio = 8 WHERE id_convenio = 108;
    UPDATE fichas SET id_convenio = 9 WHERE id_convenio = 109;

    -- Restore Credentials
    UPDATE convenios c SET usuario = t.usuario, senha_criptografada = t.senha_criptografada 
    FROM tmp_creds t WHERE c.nome = t.nome;

    -- Cleanup TEMP ones
    DELETE FROM convenios WHERE id_convenio IN (102, 103, 106, 108, 109);

    -- 4. Final Constraints
    ALTER TABLE convenios DROP CONSTRAINT IF EXISTS unique_convenio_nome;
    ALTER TABLE convenios ADD CONSTRAINT unique_convenio_nome UNIQUE (nome);
    
    -- Sync sequence
    PERFORM setval('convenios_id_convenio_seq', (SELECT MAX(id_convenio) FROM convenios));

END $$;
