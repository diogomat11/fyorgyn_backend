-- Migration 0030: Add Schema and Triggers for Guias and Agendamentos Integration

-- 1. Adicionar colunas
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS numero_guia VARCHAR;
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS data_update TIMESTAMP WITH TIME ZONE DEFAULT NOW();
ALTER TABLE base_guias ADD COLUMN IF NOT EXISTS saldo INTEGER NOT NULL DEFAULT 0;

-- 2. Trigger: trg_agendamento_define_cod_faturamento
CREATE OR REPLACE FUNCTION func_agendamento_define_cod_faturamento()
RETURNS TRIGGER AS $$
DECLARE
    v_cod_fat VARCHAR;
    v_valor NUMERIC;
    v_id_proc INTEGER;
BEGIN
    -- Se cod_procedimento_fat for NULL ou cod_procedimento_aut for alterado
    IF (NEW.cod_procedimento_fat IS NULL) OR (TG_OP = 'UPDATE' AND NEW.cod_procedimento_aut IS DISTINCT FROM OLD.cod_procedimento_aut) THEN
        IF NEW.id_convenio IS NOT NULL AND NEW.cod_procedimento_aut IS NOT NULL THEN
            SELECT pf.faturamento, pf.id_procedimento, pfat.valor 
            INTO v_cod_fat, v_id_proc, v_valor
            FROM procedimentos pf
            LEFT JOIN procedimento_faturamento pfat ON pfat.id_procedimento = pf.id_procedimento AND pfat.id_convenio = NEW.id_convenio
            WHERE pf.id_convenio = NEW.id_convenio AND pf.autorizacao = NEW.cod_procedimento_aut
            LIMIT 1;

            IF FOUND THEN
                NEW.cod_procedimento_fat := v_cod_fat;
                NEW.id_procedimento := v_id_proc;
                NEW.valor_procedimento := v_valor;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agendamento_define_cod_faturamento ON agendamentos;
CREATE TRIGGER trg_agendamento_define_cod_faturamento
BEFORE INSERT OR UPDATE ON agendamentos
FOR EACH ROW EXECUTE FUNCTION func_agendamento_define_cod_faturamento();


-- 3. Trigger: trg_define_saldo_guia
CREATE OR REPLACE FUNCTION func_define_saldo_guia()
RETURNS TRIGGER AS $$
BEGIN
    -- Unimed Goiania (3) e Unimed Anapolis (6)
    IF NEW.id_convenio IN (3, 6) THEN
        -- Sobrescreve saldo com qtdeAut (Tanto INSERT quanto UPDATE)
        IF NEW.qtde_solicitada IS NOT NULL THEN
            NEW.saldo := NEW.qtde_solicitada;
        ELSE
            NEW.saldo := 0;
        END IF;
    ELSE
        -- Demais convenios: Apenas no INSERT pega a qtde inicial.
        IF TG_OP = 'INSERT' THEN
            IF NEW.qtde_solicitada IS NOT NULL THEN
                NEW.saldo := NEW.qtde_solicitada;
            ELSE
                NEW.saldo := 0;
            END IF;
        END IF;
        -- No UPDATE matem o saldo atual para os demais = nao faz nada
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_define_saldo_guia ON base_guias;
CREATE TRIGGER trg_define_saldo_guia
BEFORE INSERT OR UPDATE ON base_guias
FOR EACH ROW EXECUTE FUNCTION func_define_saldo_guia();


-- 4. Trigger: trg_vincula_guia_a_agendamento
CREATE OR REPLACE FUNCTION func_vincula_guia_a_agendamento()
RETURNS TRIGGER AS $$
DECLARE
    rec_agendamento RECORD;
    v_saldo INTEGER;
BEGIN
    v_saldo := NEW.saldo;

    IF v_saldo > 0 AND NEW.status_guia <> 'Cancelada' AND NEW.status_guia <> 'Negada' THEN
        
        FOR rec_agendamento IN
            SELECT id_agendamento FROM agendamentos 
            WHERE numero_guia IS NULL
            AND id_carteirinha = NEW.carteirinha_id
            AND id_convenio = NEW.id_convenio
            AND cod_procedimento_aut = NEW.codigo_terapia
            AND Status NOT IN ('Falta', 'Cancelado')
            AND data >= NEW.data_autorizacao
            AND data <= NEW.validade
            ORDER BY 
                CASE WHEN NEW.id_convenio IN (3, 6) THEN data END DESC,
                data ASC
        LOOP
            -- Atualiza o agendamento associando-o a esta guia
            UPDATE agendamentos 
            SET numero_guia = NEW.guia
            WHERE id_agendamento = rec_agendamento.id_agendamento;

            -- Decrementa Saldo Localmente
            v_saldo := v_saldo - 1;
            
            -- Se o saldo zerou, para o loop
            IF v_saldo <= 0 THEN
                EXIT;
            END IF;
        END LOOP;
        
        -- Se consumiu saldo nestas vinculacoes, atualizamos a diferenca no saldo
        IF v_saldo <> NEW.saldo THEN
            NEW.saldo := v_saldo;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_z_vincula_guia_a_agendamento ON base_guias;
CREATE TRIGGER trg_z_vincula_guia_a_agendamento
BEFORE INSERT OR UPDATE ON base_guias
FOR EACH ROW EXECUTE FUNCTION func_vincula_guia_a_agendamento();
