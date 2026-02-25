-- Migration 0036: Correcoes das Regras de Triggers de Lincagem de Agendamentos e Guias
-- 1. Altera Hardcoded (3, 6) para (2, 3) correspondente a Unimed Anapolis e Goiania.
-- 2. Junta Agendamento com Base Guia atravez de carteirinhas.id_paciente ao inves de id_carteirinha vazio.
-- 3. Preserva o Saldo local de (2, 3) intacto (sendo consumido apenas no update web scraping remoto).

-- A. Atualiza trg_define_saldo_guia
CREATE OR REPLACE FUNCTION func_define_saldo_guia()
RETURNS TRIGGER AS $$
BEGIN
    -- Unimed Anapolis (2) e Unimed Goiania (3)
    IF NEW.id_convenio IN (2, 3) THEN
        -- Sobrescreve saldo com qtde_solicitada (Tanto INSERT quanto UPDATE originado do Robô)
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
        -- No UPDATE mantem o saldo atual para os demais = nao subscreve o saldo local
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- B. Atualiza trg_z_vincula_guia_a_agendamento
CREATE OR REPLACE FUNCTION func_vincula_guia_a_agendamento()
RETURNS TRIGGER AS $$
DECLARE
    rec_agendamento RECORD;
    v_saldo INTEGER;
    v_id_paciente INTEGER;
BEGIN
    v_saldo := NEW.saldo;

    -- Descobrir qual o id_paciente dono desta guia atraves de sua carteirinha
    SELECT id_paciente INTO v_id_paciente FROM carteirinhas WHERE id = NEW.carteirinha_id;

    IF v_saldo > 0 AND NEW.status_guia <> 'Cancelada' AND NEW.status_guia <> 'Negada' AND v_id_paciente IS NOT NULL THEN
        
        -- Busca agendamentos do paciente que batem com as restricoes da Guia
        FOR rec_agendamento IN
            SELECT id_agendamento FROM agendamentos 
            WHERE numero_guia IS NULL
            AND id_paciente = v_id_paciente
            AND id_convenio = NEW.id_convenio
            AND cod_procedimento_aut = NEW.codigo_terapia
            AND "Status" NOT IN ('Falta', 'Cancelado')
            AND data >= NEW.data_autorizacao
            AND data <= NEW.validade
            ORDER BY 
                CASE WHEN NEW.id_convenio IN (2, 3) THEN data END DESC,
                data ASC
        LOOP
            -- Atualiza o agendamento associando-o a esta guia
            UPDATE agendamentos 
            SET numero_guia = NEW.guia
            WHERE id_agendamento = rec_agendamento.id_agendamento;

            -- Decrementa Saldo Localmente na memoria da Trigger
            v_saldo := v_saldo - 1;
            
            -- Se o saldo zerou, para o loop de vincular outras consultas
            IF v_saldo <= 0 THEN
                EXIT;
            END IF;
        END LOOP;
        
        -- Aplica a subtracao fisica na base se consumiu saldo e NAO FOR unimed anapolis/goiania.
        -- Para (2, 3), o Saldo é sacrossanto vindo da Operadora apenas.
        IF v_saldo <> NEW.saldo AND NEW.id_convenio NOT IN (2, 3) THEN
            NEW.saldo := v_saldo;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
