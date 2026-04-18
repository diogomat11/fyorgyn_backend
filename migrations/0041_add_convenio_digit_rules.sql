-- Migration 41: Regras dinâmicas de formatação de Carteirinhas por Convênio
-- Adiciona as colunas digitos_carteirinha e codigo_referenciado

ALTER TABLE convenios ADD COLUMN IF NOT EXISTS digitos_carteirinha INTEGER;
ALTER TABLE convenios ADD COLUMN IF NOT EXISTS codigo_referenciado TEXT;

-- Seeding inicial das restrições de dígitos
UPDATE convenios SET digitos_carteirinha = 21 WHERE nome ILIKE '%UNIMED%';
UPDATE convenios SET digitos_carteirinha = 9  WHERE nome ILIKE '%IPASGO%';
UPDATE convenios SET digitos_carteirinha = 9  WHERE nome ILIKE '%AMIL%';
UPDATE convenios SET digitos_carteirinha = 20 WHERE nome ILIKE '%SULAMERICA%';
UPDATE convenios SET digitos_carteirinha = 9  WHERE nome ILIKE '%TEST%';

-- Fallback padrão para convênios sem regra predefinida
UPDATE convenios SET digitos_carteirinha = 21 WHERE digitos_carteirinha IS NULL;
