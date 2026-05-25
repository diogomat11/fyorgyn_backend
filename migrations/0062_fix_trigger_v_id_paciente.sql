-- Migration 0062: Fix func_vincula_guia_a_agendamento trigger parameter type
-- Reason: id_paciente was changed to TEXT in Migration 0061, so v_id_paciente in the trigger must be TEXT.

CREATE OR REPLACE FUNCTION func_vincula_guia_a_agendamento()
RETURNS TRIGGER AS $$
DECLARE
    rec_agendamento RECORD;
    v_saldo INTEGER;
    v_id_paciente TEXT; -- Changed from INTEGER to TEXT
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
