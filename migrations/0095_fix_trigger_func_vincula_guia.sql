CREATE OR REPLACE FUNCTION public.func_vincula_guia_a_agendamento()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    rec_agendamento RECORD;
    v_saldo INTEGER;
    v_id_paciente TEXT;
BEGIN
    v_saldo := NEW.saldo;

    SELECT id_paciente::text INTO v_id_paciente FROM carteirinhas WHERE id = NEW.carteirinha_id;

    IF v_saldo > 0 AND NEW.status_guia <> 'Cancelada' AND NEW.status_guia <> 'Negada' AND v_id_paciente IS NOT NULL AND v_id_paciente <> '' THEN
        
        FOR rec_agendamento IN
            SELECT id_agendamento FROM agendamentos 
            WHERE numero_guia IS NULL
            AND id_paciente::text = v_id_paciente
            AND id_convenio = NEW.id_convenio
            AND cod_procedimento_aut = NEW.codigo_terapia
            AND "Status" NOT IN ('Falta', 'Cancelado')
            AND data >= NEW.data_autorizacao
            AND data <= NEW.validade
            AND (NEW.id_convenio <> 6 OR user_id = NEW.user_id OR cod_prestador = NEW.cod_prestador)
            ORDER BY 
                CASE WHEN NEW.id_convenio IN (2, 3) THEN data END DESC,
                data ASC
        LOOP
            UPDATE agendamentos 
            SET numero_guia = NEW.guia
            WHERE id_agendamento = rec_agendamento.id_agendamento;

            v_saldo := v_saldo - 1;
            
            IF v_saldo <= 0 THEN
                EXIT;
            END IF;
        END LOOP;
        
        IF v_saldo <> NEW.saldo AND NEW.id_convenio NOT IN (2, 3) THEN
            NEW.saldo := v_saldo;
        END IF;
    END IF;

    RETURN NEW;
END;
$function$;
