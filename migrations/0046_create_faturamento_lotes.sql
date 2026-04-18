CREATE TABLE IF NOT EXISTS faturamento_lotes (
    id SERIAL PRIMARY KEY,
    "loteId" INTEGER,
    "detalheId" INTEGER UNIQUE NOT NULL,
    "CodigoBeneficiario" TEXT,
    "StatusConciliacao" TEXT DEFAULT 'pendente',
    "dataRealizacao" DATE,
    "Guia" TEXT,
    "StatusConferencia" INTEGER,
    "ValorProcedimento" DOUBLE PRECISION,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_faturamento_lotes_id ON faturamento_lotes (id);
CREATE INDEX IF NOT EXISTS ix_faturamento_lotes_loteId ON faturamento_lotes ("loteId");
CREATE UNIQUE INDEX IF NOT EXISTS ix_faturamento_lotes_detalheId ON faturamento_lotes ("detalheId");
