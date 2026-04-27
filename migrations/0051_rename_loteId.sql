ALTER TABLE faturamento_lotes RENAME COLUMN "loteId" TO id_lote;

-- Drop old index and create a new one
DROP INDEX IF EXISTS ix_faturamento_lotes_loteId;
CREATE INDEX IF NOT EXISTS ix_faturamento_lotes_id_lote ON faturamento_lotes (id_lote);

-- Add foreign key reference to lotes_convenio
ALTER TABLE faturamento_lotes
ADD CONSTRAINT fk_faturamento_lotes_id_lote
FOREIGN KEY (id_lote)
REFERENCES lotes_convenio(id_lote)
ON DELETE SET NULL;
