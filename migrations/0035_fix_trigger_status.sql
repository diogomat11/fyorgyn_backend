-- Migration 0035: Corrigir case-sensitivity da coluna "Status" no Trigger
-- PostgreSQL converte nomes sem aspas para lowercase. O Mapeamento SQLAlchemy criou "Status" com maiúscula.

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
            AND "Status" NOT IN ('Falta', 'Cancelado')
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
