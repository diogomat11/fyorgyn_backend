-- Migration 0024: Fix PEI trigger to match ALL Unimed convenios
-- Previously used ORDER BY id_convenio ASC LIMIT 1, which only matched UNIMED ANAPOLIS (id=2)
-- Now uses NOT IN (SELECT ...) to match any convenio with UNIMED in the name

CREATE OR REPLACE FUNCTION calculate_patient_pei() RETURNS TRIGGER AS $$
DECLARE
    target_carteirinha_id INTEGER;
    target_codigo_terapia TEXT;
    target_id_convenio INTEGER;
    
    latest_guia_id INTEGER;
    latest_data_autorizacao DATE;
    latest_qtde INTEGER;
    
    override_val FLOAT;
    
    final_pei FLOAT;
    final_status TEXT;
    final_validade DATE;
BEGIN
    -- 1. Determine Target Context
    IF TG_TABLE_NAME = 'base_guias' THEN
        target_carteirinha_id := NEW.carteirinha_id;
        target_codigo_terapia := NEW.codigo_terapia;
    ELSIF TG_TABLE_NAME = 'pei_temp' THEN
        SELECT carteirinha_id, codigo_terapia INTO target_carteirinha_id, target_codigo_terapia
        FROM base_guias WHERE id = NEW.base_guia_id;
    END IF;

    IF target_carteirinha_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- 2. Check Convenio: Only Unimed (match ANY Unimed convenio)
    SELECT id_convenio INTO target_id_convenio FROM carteirinhas WHERE id = target_carteirinha_id;
    
    IF target_id_convenio NOT IN (
        SELECT id_convenio FROM convenios WHERE nome ILIKE '%UNIMED%'
    ) THEN
        RETURN NEW;
    END IF;

    -- 3. Find Latest Guia
    SELECT id, data_autorizacao, qtde_solicitada 
    INTO latest_guia_id, latest_data_autorizacao, latest_qtde
    FROM base_guias
    WHERE carteirinha_id = target_carteirinha_id 
      AND codigo_terapia = target_codigo_terapia
    ORDER BY data_autorizacao DESC, id DESC
    LIMIT 1;

    IF latest_guia_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- 4. Check for Override
    SELECT pei_semanal INTO override_val
    FROM pei_temp
    WHERE base_guia_id = latest_guia_id;

    -- 5. Calculate
    final_status := 'Pendente';
    final_pei := 0.0;
    
    IF latest_data_autorizacao IS NOT NULL THEN
        final_validade := latest_data_autorizacao + INTERVAL '180 days';
    ELSE
        final_validade := NULL;
    END IF;

    IF override_val IS NOT NULL THEN
        final_pei := override_val;
        final_status := 'Validado';
    ELSE
        IF latest_qtde IS NOT NULL AND latest_qtde > 0 THEN
            final_pei := latest_qtde::FLOAT / 16.0;
            IF final_pei = FLOOR(final_pei) THEN
                final_status := 'Validado';
            ELSE
                final_status := 'Pendente';
            END IF;
        ELSE
            final_pei := 0.0;
            final_status := 'Pendente';
        END IF;
    END IF;

    -- 6. Upsert into patient_pei
    UPDATE patient_pei 
    SET base_guia_id = latest_guia_id,
        pei_semanal = final_pei,
        validade = final_validade,
        status = final_status,
        updated_at = NOW()
    WHERE carteirinha_id = target_carteirinha_id AND codigo_terapia = target_codigo_terapia;
    
    IF NOT FOUND THEN
        INSERT INTO patient_pei (carteirinha_id, codigo_terapia, base_guia_id, pei_semanal, validade, status, updated_at)
        VALUES (target_carteirinha_id, target_codigo_terapia, latest_guia_id, final_pei, final_validade, final_status, NOW());
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
