-- Migration 0053: Performance indexes for conciliação
-- Índices para acelerar JOINs e filtros de conciliação

-- Agendamentos: filtros comuns
CREATE INDEX IF NOT EXISTS idx_agendamentos_status ON agendamentos("Status");
CREATE INDEX IF NOT EXISTS idx_agendamentos_numero_guia ON agendamentos(numero_guia);
CREATE INDEX IF NOT EXISTS idx_agendamentos_data ON agendamentos(data);
CREATE INDEX IF NOT EXISTS idx_agendamentos_convenio_status_data ON agendamentos(id_convenio, "Status", data);
CREATE INDEX IF NOT EXISTS idx_agendamentos_carteirinha_text ON agendamentos(carteirinha);
CREATE INDEX IF NOT EXISTS idx_agendamentos_id_carteirinha ON agendamentos(id_carteirinha);

-- Carteirinhas: busca por texto e codigo_beneficiario
CREATE INDEX IF NOT EXISTS idx_carteirinhas_codigo_beneficiario ON carteirinhas(codigo_beneficiario);
CREATE INDEX IF NOT EXISTS idx_carteirinhas_carteirinha ON carteirinhas(carteirinha);

-- FaturamentoLote: filtros de conciliação
CREATE INDEX IF NOT EXISTS idx_fat_lotes_guia ON faturamento_lotes("Guia");
CREATE INDEX IF NOT EXISTS idx_fat_lotes_cod_beneficiario ON faturamento_lotes("CodigoBeneficiario");
CREATE INDEX IF NOT EXISTS idx_fat_lotes_lote_agendamento ON faturamento_lotes(id_lote, agendamento_id);

-- BaseGuias: busca por guia
CREATE INDEX IF NOT EXISTS idx_base_guias_guia ON base_guias(guia);

-- Lote Agendamento Itens: filtros + status
CREATE INDEX IF NOT EXISTS idx_lai_lote_status ON lote_agendamento_itens(id_lote_ag, status_conciliacao);
