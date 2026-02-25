CREATE TABLE IF NOT EXISTS convenio_operacoes (
    id SERIAL PRIMARY KEY,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    descricao TEXT NOT NULL,
    valor TEXT NOT NULL
);

-- Seed defaults for Unimed Goiania (3)
INSERT INTO convenio_operacoes (id_convenio, descricao, valor)
VALUES (3, 'Padrão (Consulta)', '');

-- Seed defaults for Unimed Anapolis (2)
INSERT INTO convenio_operacoes (id_convenio, descricao, valor)
VALUES 
    (2, 'Padrão (Consulta)', ''),
    (2, 'OP=1 (Consulta Específica/Forçada)', '1'),
    (2, 'OP=2 (Captura Guias)', '2');
