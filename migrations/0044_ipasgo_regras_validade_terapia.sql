-- Migration 0044: Regras de Negócio Específicas IPASGO e Correção de Terapia
-- 1. Cria Trigger para preencher a Validade (+45 dias) se convenio=6, status='Autorizado' e validade for nula
-- 2. Atualiza função do Nome da Terapia para tolerância de Zeros à Esquerda (ex: '030101010' casar com '30101010')

-- PARTE 1: VALIDADE DO IPASGO
CREATE OR REPLACE FUNCTION func_preenche_validade_ipasgo()
RETURNS TRIGGER AS $$
BEGIN
    -- Regra Exclusiva para IPASGO (ID 6)
    IF NEW.id_convenio = 6 THEN
        IF UPPER(NEW.status_guia) = 'AUTORIZADO' AND NEW.validade IS NULL AND NEW.data_autorizacao IS NOT NULL THEN
            NEW.validade := NEW.data_autorizacao + INTERVAL '45 days';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_preenche_validade_ipasgo ON base_guias;
CREATE TRIGGER trg_preenche_validade_ipasgo
BEFORE INSERT OR UPDATE OF status_guia, validade, data_autorizacao ON base_guias
FOR EACH ROW
EXECUTE FUNCTION func_preenche_validade_ipasgo();


-- PARTE 2: NOME DA TERAPIA TOLERANTE (Castando LTRIM Zero)
CREATE OR REPLACE FUNCTION func_preenche_nome_terapia()
RETURNS TRIGGER AS $$
DECLARE
    v_nome_procedimento TEXT;
BEGIN
    IF NEW.codigo_terapia IS NOT NULL AND (TG_OP = 'INSERT' OR OLD.codigo_terapia IS DISTINCT FROM NEW.codigo_terapia OR NEW.nome_terapia IS NULL) THEN
        -- Busca nome na tabela procedimentos ignorando Zeros a esquerda e espacos
        SELECT nome INTO v_nome_procedimento 
        FROM procedimentos 
        WHERE LTRIM(TRIM(codigo_procedimento), '0') = LTRIM(TRIM(NEW.codigo_terapia), '0')
        AND (id_convenio IS NULL OR id_convenio = NEW.id_convenio)
        LIMIT 1;

        IF v_nome_procedimento IS NOT NULL THEN
            NEW.nome_terapia := v_nome_procedimento;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Já há um trigger referenciando ele (trg_preenche_nome_terapia). APenas usar OR REPLACE substitui a logica.
-- Atualizar retroativamente os vazios
UPDATE base_guias bg
SET nome_terapia = sub.nome
FROM (
    SELECT bg_inner.id, p.nome
    FROM base_guias bg_inner
    JOIN procedimentos p 
      ON LTRIM(TRIM(p.codigo_procedimento), '0') = LTRIM(TRIM(bg_inner.codigo_terapia), '0')
     AND (p.id_convenio IS NULL OR p.id_convenio = bg_inner.id_convenio)
    WHERE bg_inner.nome_terapia IS NULL AND bg_inner.codigo_terapia IS NOT NULL
) sub
WHERE bg.id = sub.id;
