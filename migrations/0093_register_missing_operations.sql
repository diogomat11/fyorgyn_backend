-- ============================================================
-- Migration 0093: Registrar Operações Faltantes de Integradores (Bradesco & Unimed Goiânia)
-- ============================================================

INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES 
    (1, 'op1_solicitar_autorizacao', 'Solicitação de novas autorizações de guias no portal Bradesco', 'local', TRUE, 'automatico'),
    (1, 'op1_consultar_guias', 'Consulta e importação de guias e demonstrativos de faturamento', 'local', TRUE, 'automatico')
ON CONFLICT DO NOTHING;

INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES 
    (3, 'op4_finalizados', 'Conferência e monitoramento de guias finalizadas/faturadas', 'local', TRUE, 'automatico')
ON CONFLICT DO NOTHING;
