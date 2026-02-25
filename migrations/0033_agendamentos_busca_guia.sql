-- Migration 0033: Adiciona Trigger AFTER nos Agendamentos para acordar Guias
-- Quando um agendamento novo chegar (ou for alterado codigos autorizacao), 
-- o Gatilho BEFORE ja arrumou os codigos OP3. Esse gatilho AFTER obriga 
-- ativamente as guias elegiveis a dispararem suas proprias verificacoes de saldo e match de volta,
-- prevenindo a orfandade em inserts via CSV ou Forms novos.

CREATE OR REPLACE FUNCTION func_agendamento_acorda_guia()
RETURNS TRIGGER AS $$
BEGIN
    -- Se o agendamento nao possui guia e seu status esta passivel de uso:
    IF NEW.numero_guia IS NULL AND NEW."Status" NOT IN ('Falta', 'Cancelado') THEN
        -- Vamo tocar (UPDATE) em qualquer Guia que possa servir pra esse agendamento.
        -- Esse toque inocente no "updated_at" fará o banco de dados disparar autonomamente
        -- o Trigger da propria Guia (trg_z_vincula_guia_a_agendamento), que fara todo o calculo de Saldo 
        -- e dara o UPDATE de volta no Agendamento perfeitamente blindado sob lock de concorrencia.
        
        UPDATE base_guias 
        SET updated_at = NOW() 
        WHERE carteirinha_id = NEW.id_carteirinha 
        AND id_convenio = NEW.id_convenio
        AND codigo_terapia = NEW.cod_procedimento_aut
        AND data_autorizacao <= NEW.data
        AND validade >= NEW.data
        AND saldo > 0
        AND status_guia NOT IN ('Cancelada', 'Negada');
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agendamento_acorda_guia ON agendamentos;
CREATE TRIGGER trg_agendamento_acorda_guia
AFTER INSERT OR UPDATE ON agendamentos
FOR EACH ROW EXECUTE FUNCTION func_agendamento_acorda_guia();
