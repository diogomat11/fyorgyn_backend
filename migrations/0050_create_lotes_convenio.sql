CREATE TABLE IF NOT EXISTS lotes_convenio (
    id_lote SERIAL PRIMARY KEY,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    numero_lote INTEGER,
    cod_prestador TEXT,
    status TEXT DEFAULT 'Aberto',
    data_inicio DATE,
    data_fim DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_lotes_convenio_numero_lote ON lotes_convenio(numero_lote);

-- Adiciona operações 13 e 14 ao IPASGO se não existirem
INSERT INTO convenio_operacoes (id_convenio, descricao, valor)
SELECT 6, 'Criar Lote de Faturamento (API)', '13'
WHERE NOT EXISTS (SELECT 1 FROM convenio_operacoes WHERE id_convenio = 6 AND valor = '13');

INSERT INTO convenio_operacoes (id_convenio, descricao, valor)
SELECT 6, 'Cancelar Lote de Faturamento (API)', '14'
WHERE NOT EXISTS (SELECT 1 FROM convenio_operacoes WHERE id_convenio = 6 AND valor = '14');
