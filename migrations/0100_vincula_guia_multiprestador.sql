-- ============================================================
-- Migration 0100: Vinculacao de guias por regra multiprestador (config)
-- ============================================================
-- Description:
--   Generaliza a regra de vinculo guia <-> agendamento do trigger
--   func_vincula_guia_a_agendamento usando a flag convenios.guias_multiprestador
--   (migration 0096), substituindo o hardcode por id_convenio (antigo: restricao
--   apenas para IPASGO id_convenio=6).
--
--   Regra nova:
--   - guias_multiprestador = TRUE  (ex. Unimed Goiania, id 3): saldo da guia
--     compartilhado entre prestadores com vinculo carteirinha -> sem restricao
--     de user/cod_prestador no vinculo (comportamento historico).
--   - guias_multiprestador = FALSE (demais, ex. IPASGO): guia de uso restrito ao
--     cod_prestador de origem -> vinculo apenas se mesmo user_id OU mesmo
--     cod_prestador do agendamento.
--
--   A regra de subtracao fisica de saldo (id_convenio NOT IN (2,3)) e a
--   ordenacao especifica Unimed permanecem inalteradas.

CREATE OR REPLACE FUNCTION public.func_vincula_guia_a_agendamento()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    rec_agendamento RECORD;
    v_saldo INTEGER;
    v_id_paciente TEXT;
    v_multiprestador BOOLEAN;
BEGIN
    v_saldo := NEW.saldo;

    SELECT id_paciente::text INTO v_id_paciente FROM carteirinhas WHERE id = NEW.carteirinha_id;

    -- Regra multiprestador por convenio (0096). Convenio ausente -> restritivo (FALSE).
    SELECT COALESCE(guias_multiprestador, FALSE) INTO v_multiprestador
    FROM convenios WHERE id_convenio = NEW.id_convenio;

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
            AND (
                v_multiprestador
                OR user_id = NEW.user_id
                OR cod_prestador = NEW.cod_prestador
            )
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
