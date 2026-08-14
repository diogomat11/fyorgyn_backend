import os
import psycopg2
from dotenv import load_dotenv

load_dotenv("backend/.env")
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("ERRO: DATABASE_URL não definida")
    exit(1)

sql_commands = """
-- 1. Atualizar func_vincula_guia_a_carteirinha_ipasgo para respeitar o tenant (user_id)
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
        AND (user_id = NEW.user_id OR NEW.user_id IS NULL OR user_id IS NULL)
        LIMIT 1;

        IF v_carteirinha_id IS NOT NULL THEN
            NEW.carteirinha_id := v_carteirinha_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Atualizar func_vincula_carteirinha_a_guias_orfas_ipasgo para respeitar o tenant
CREATE OR REPLACE FUNCTION func_vincula_carteirinha_a_guias_orfas_ipasgo()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id_convenio = 6 AND NEW.codigo_beneficiario IS NOT NULL THEN
        UPDATE base_guias 
        SET carteirinha_id = NEW.id 
        WHERE id_convenio = 6 
        AND codigo_beneficiario = NEW.codigo_beneficiario 
        AND carteirinha_id IS NULL
        AND (user_id = NEW.user_id OR NEW.user_id IS NULL OR user_id IS NULL);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Atualizar func_vincula_guia_a_agendamento para restringir IPASGO ao tenant/prestador
CREATE OR REPLACE FUNCTION func_vincula_guia_a_agendamento()
RETURNS TRIGGER AS $$
DECLARE
    rec_agendamento RECORD;
    v_saldo INTEGER;
    v_id_paciente INTEGER;
BEGIN
    v_saldo := NEW.saldo;

    SELECT id_paciente INTO v_id_paciente FROM carteirinhas WHERE id = NEW.carteirinha_id;

    IF v_saldo > 0 AND NEW.status_guia <> 'Cancelada' AND NEW.status_guia <> 'Negada' AND v_id_paciente IS NOT NULL THEN
        
        FOR rec_agendamento IN
            SELECT id_agendamento FROM agendamentos 
            WHERE numero_guia IS NULL
            AND id_paciente = v_id_paciente
            AND id_convenio = NEW.id_convenio
            AND cod_procedimento_aut = NEW.codigo_terapia
            AND "Status" NOT IN ('Falta', 'Cancelado')
            AND data >= NEW.data_autorizacao
            AND data <= NEW.validade
            -- Restrição IPASGO: Garantir que não vincule agendamento de outro tenant/prestador
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
$$ LANGUAGE plpgsql;
"""

try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute(sql_commands)
    cursor.close()
    conn.close()
    print("Triggers atualizados com sucesso para respeitar o tenant IPASGO!")
except Exception as e:
    print(f"Erro ao atualizar triggers: {e}")
