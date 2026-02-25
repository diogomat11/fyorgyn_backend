-- Migration 0034: Drop Trigger automatico de Busca de guias em Agendamentos
-- As guias serao vinculadas apenas de maneira intencional via Job/Manual na API do Frontend
-- e nao assincronamente a cada insert de Agendamento.

DROP TRIGGER IF EXISTS trg_agendamento_acorda_guia ON agendamentos;
DROP FUNCTION IF EXISTS func_agendamento_acorda_guia();
