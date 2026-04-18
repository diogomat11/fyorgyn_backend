-- Migration 42: Refatoração IPASGO (Guias e Beneficiários)
-- 1. Renomear cod_convenio para codigo_beneficiario na tabela carteirinhas
-- 2. Adicionar as colunas codigo_beneficiario e nome_terapia na tabela base_guias
-- 3. Criar Triggers para Relacionamento Bi-direcional Guias <-> Beneficiario

-- Passo 1: Renomear a coluna em carteirinhas (se já não estiver feito)
DO $$ 
BEGIN
  IF EXISTS(SELECT * FROM information_schema.columns WHERE table_name='carteirinhas' AND column_name='cod_convenio') THEN
      ALTER TABLE carteirinhas RENAME COLUMN cod_convenio TO codigo_beneficiario;
  END IF;
END $$;

-- Passo 2: Adicionar novas colunas em base_guias
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS codigo_beneficiario TEXT;
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS nome_terapia TEXT;

-- Passo 3: Funções e Triggers Bi-direcionais

-- A. Trigger: Quando inserir/atualizar base_guias com codigo_beneficiario IPASGO (Convenio 6), auto-preencher carteirinha_id
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
        LIMIT 1;

        IF v_carteirinha_id IS NOT NULL THEN
            NEW.carteirinha_id := v_carteirinha_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vincula_guia_a_carteirinha_ipasgo ON base_guias;
CREATE TRIGGER trg_vincula_guia_a_carteirinha_ipasgo
BEFORE INSERT OR UPDATE OF codigo_beneficiario ON base_guias
FOR EACH ROW
EXECUTE FUNCTION func_vincula_guia_a_carteirinha_ipasgo();

-- B. Trigger: Quando inserir/atualizar carteirinhas (IPASGO) com codigo_beneficiario, vincular guias órfãs retroativas
CREATE OR REPLACE FUNCTION func_vincula_carteirinha_a_guias_orfas_ipasgo()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id_convenio = 6 AND NEW.codigo_beneficiario IS NOT NULL THEN
        -- Se esta carteirinha acabou de ganhar um codigo_beneficiario, varre base_guias e amarra guias perdidas
        UPDATE base_guias 
        SET carteirinha_id = NEW.id 
        WHERE id_convenio = 6 
        AND codigo_beneficiario = NEW.codigo_beneficiario 
        AND carteirinha_id IS NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vincula_carteirinha_a_guias_orfas_ipasgo ON carteirinhas;
CREATE TRIGGER trg_vincula_carteirinha_a_guias_orfas_ipasgo
AFTER INSERT OR UPDATE OF codigo_beneficiario ON carteirinhas
FOR EACH ROW
EXECUTE FUNCTION func_vincula_carteirinha_a_guias_orfas_ipasgo();

-- C. Trigger: Quando inserir/atualizar codigo_terapia em base_guias, buscar e fixar o nome_terapia
CREATE OR REPLACE FUNCTION func_preenche_nome_terapia()
RETURNS TRIGGER AS $$
DECLARE
    v_nome_procedimento TEXT;
BEGIN
    IF NEW.codigo_terapia IS NOT NULL AND (TG_OP = 'INSERT' OR OLD.codigo_terapia IS DISTINCT FROM NEW.codigo_terapia OR NEW.nome_terapia IS NULL) THEN
        -- Busca nome na tabela procedimentos
        SELECT nome INTO v_nome_procedimento 
        FROM procedimentos 
        WHERE codigo_procedimento = NEW.codigo_terapia 
        -- Limitamos pelo id_convenio atual se quisermos ser restritos, mas como codigo_procedimento costuma ser TUSS universal ou Tabela Própria, usamos o primeiro.
        AND (id_convenio IS NULL OR id_convenio = NEW.id_convenio)
        LIMIT 1;

        IF v_nome_procedimento IS NOT NULL THEN
            NEW.nome_terapia := v_nome_procedimento;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_preenche_nome_terapia ON base_guias;
CREATE TRIGGER trg_preenche_nome_terapia
BEFORE INSERT OR UPDATE OF codigo_terapia ON base_guias
FOR EACH ROW
EXECUTE FUNCTION func_preenche_nome_terapia();
