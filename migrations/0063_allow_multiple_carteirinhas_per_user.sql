-- Migration 0063: Allow duplicate carteirinhas for different users
-- Reason: Patients/Cards can be linked to one or more user_ids, each with their own patient ID system.

-- 1. Drop existing global uniqueness constraint on carteirinha
ALTER TABLE carteirinhas DROP CONSTRAINT IF EXISTS carteirinhas_carteirinha_key;

-- 2. Add composite unique constraint on (carteirinha, user_id)
ALTER TABLE carteirinhas ADD CONSTRAINT uq_carteirinha_user_id UNIQUE (carteirinha, user_id);

-- 3. Update IPASGO trigger function: func_vincula_guia_a_carteirinha_ipasgo
CREATE OR REPLACE FUNCTION func_vincula_guia_a_carteirinha_ipasgo()
RETURNS TRIGGER AS $$
DECLARE
    v_carteirinha_id INTEGER;
BEGIN
    IF NEW.id_convenio = 6 AND NEW.codigo_beneficiario IS NOT NULL THEN
        SELECT id INTO v_carteirinha_id 
        FROM carteirinhas 
        WHERE id_convenio = 6 
        AND codigo_beneficiario = NEW.codigo_beneficiario 
        AND (user_id IS NOT DISTINCT FROM NEW.user_id OR NEW.user_id IS NULL)
        ORDER BY (user_id = NEW.user_id) DESC, id ASC
        LIMIT 1;

        IF v_carteirinha_id IS NOT NULL THEN
            NEW.carteirinha_id := v_carteirinha_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. Update IPASGO trigger function: func_vincula_carteirinha_a_guias_orfas_ipasgo
CREATE OR REPLACE FUNCTION func_vincula_carteirinha_a_guias_orfas_ipasgo()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id_convenio = 6 AND NEW.codigo_beneficiario IS NOT NULL THEN
        -- Se esta carteirinha acabou de ganhar um codigo_beneficiario, varre base_guias e amarra guias perdidas do mesmo usuario
        UPDATE base_guias 
        SET carteirinha_id = NEW.id 
        WHERE id_convenio = 6 
        AND codigo_beneficiario = NEW.codigo_beneficiario 
        AND (user_id IS NOT DISTINCT FROM NEW.user_id OR NEW.user_id IS NULL OR user_id IS NULL)
        AND carteirinha_id IS NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
