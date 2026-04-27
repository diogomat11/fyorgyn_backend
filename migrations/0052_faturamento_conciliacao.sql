-- Migration 0052: Faturamento Conciliação
-- Adiciona agendamento_id a faturamento_lotes
-- Cria tabelas lotes_agendamento e lote_agendamento_itens

-- 1. Adicionar agendamento_id à faturamento_lotes
ALTER TABLE faturamento_lotes
  ADD COLUMN IF NOT EXISTS agendamento_id INTEGER REFERENCES agendamentos(id_agendamento) ON DELETE SET NULL;

-- 2. Criar tabela de lotes de agendamentos
CREATE TABLE IF NOT EXISTS lotes_agendamento (
  id_lote_ag SERIAL PRIMARY KEY,
  id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
  data_inicio DATE,
  data_fim DATE,
  status TEXT DEFAULT 'Aberto',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Criar tabela relacional: itens do lote de agendamento
CREATE TABLE IF NOT EXISTS lote_agendamento_itens (
  id SERIAL PRIMARY KEY,
  id_lote_ag INTEGER REFERENCES lotes_agendamento(id_lote_ag) ON DELETE CASCADE,
  id_agendamento INTEGER REFERENCES agendamentos(id_agendamento) ON DELETE CASCADE,
  status_conciliacao TEXT DEFAULT 'Não Conciliado',
  id_faturamento_lote INTEGER REFERENCES faturamento_lotes(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Índices de performance
CREATE INDEX IF NOT EXISTS idx_fat_lotes_agendamento_id ON faturamento_lotes(agendamento_id);
CREATE INDEX IF NOT EXISTS idx_lote_ag_itens_lote ON lote_agendamento_itens(id_lote_ag);
CREATE INDEX IF NOT EXISTS idx_lote_ag_itens_agendamento ON lote_agendamento_itens(id_agendamento);
CREATE INDEX IF NOT EXISTS idx_lote_ag_itens_fat ON lote_agendamento_itens(id_faturamento_lote);
