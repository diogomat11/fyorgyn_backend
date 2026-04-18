-- 0045_revert_terapia_ltrim.sql
-- Remove funcao LTRIM do zero do trigger preenche_nome_terapia

CREATE OR REPLACE FUNCTION func_preenche_nome_terapia()
RETURNS TRIGGER AS $$
DECLARE
    v_nome_procedimento TEXT;
BEGIN
    IF NEW.codigo_terapia IS NOT NULL AND (TG_OP = 'INSERT' OR OLD.codigo_terapia IS DISTINCT FROM NEW.codigo_terapia OR NEW.nome_terapia IS NULL) THEN
        -- Busca nome na tabela procedimentos removendo apenas espaços. Zeros importam.
        SELECT nome INTO v_nome_procedimento 
        FROM procedimentos 
        WHERE TRIM(codigo_procedimento) = TRIM(NEW.codigo_terapia)
        AND (id_convenio IS NULL OR id_convenio = NEW.id_convenio)
        LIMIT 1;

        IF v_nome_procedimento IS NOT NULL THEN
            NEW.nome_terapia := v_nome_procedimento;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Atualizar retroativamente os vazios
UPDATE base_guias bg
SET nome_terapia = sub.nome
FROM (
    SELECT bg_inner.id, p.nome
    FROM base_guias bg_inner
    JOIN procedimentos p 
      ON TRIM(p.codigo_procedimento) = TRIM(bg_inner.codigo_terapia)
     AND (p.id_convenio IS NULL OR p.id_convenio = bg_inner.id_convenio)
    WHERE bg_inner.nome_terapia IS NULL AND bg_inner.codigo_terapia IS NOT NULL
) sub
WHERE bg.id = sub.id;
