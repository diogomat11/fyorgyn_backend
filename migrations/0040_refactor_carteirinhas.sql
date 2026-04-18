-- Migration 40: Refatoração da tabela Carteirinhas
-- Adiciona a coluna cod_convenio (código interno do paciente no parceiro)
-- Remove a coluna obsoleta id_pagamento

ALTER TABLE carteirinhas ADD COLUMN IF NOT EXISTS cod_convenio TEXT;
ALTER TABLE carteirinhas DROP COLUMN IF EXISTS id_pagamento;
