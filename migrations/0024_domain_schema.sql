-- Migration 0024: Detailed Domain Schema
CREATE TABLE IF NOT EXISTS procedimentos (
    id_procedimento SERIAL PRIMARY KEY,
    nome TEXT,
    codigo_procedimento TEXT,
    autorizacao TEXT,
    faturamento TEXT,
    status TEXT DEFAULT 'ativo'
);

CREATE TABLE IF NOT EXISTS areas (
    id_area SERIAL PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tipo_faturamento (
    id_tipo SERIAL PRIMARY KEY,
    tipo TEXT,
    id_doc_autorizacao INTEGER,
    id_doc_faturamento INTEGER
);

CREATE TABLE IF NOT EXISTS tipo_documentos (
    id_tipo_doc SERIAL PRIMARY KEY,
    nome TEXT,
    uso TEXT
);

CREATE TABLE IF NOT EXISTS modelo_documentos (
    id_modelo SERIAL PRIMARY KEY,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    nome_doc TEXT,
    id_tipo_faturamento INTEGER REFERENCES tipo_faturamento(id_tipo)
);

CREATE TABLE IF NOT EXISTS procedimento_faturamento (
    id_proc_fat SERIAL PRIMARY KEY,
    id_procedimento INTEGER REFERENCES procedimentos(id_procedimento) ON DELETE CASCADE,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    valor FLOAT,
    data_inicio DATE,
    data_fim DATE,
    status TEXT DEFAULT 'ativo'
);

CREATE TABLE IF NOT EXISTS fichas (
    id_ficha SERIAL PRIMARY KEY,
    id_paciente INTEGER,
    id_convenio INTEGER REFERENCES convenios(id_convenio) ON DELETE CASCADE,
    id_procedimento INTEGER REFERENCES procedimentos(id_procedimento),
    id_guia INTEGER REFERENCES base_guias(id),
    status_assinatura TEXT,
    status_conciliacao TEXT
);
