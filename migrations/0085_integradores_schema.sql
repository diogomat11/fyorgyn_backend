-- Migration 0085: Camada Integrador no schema public
-- Preserva 100% das tabelas public.convenios e public.convenio_operacoes existentes

CREATE TABLE IF NOT EXISTS public.integradores (
    id_integrador SERIAL PRIMARY KEY,
    id_convenio INT NOT NULL REFERENCES public.convenios(id_convenio) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    sigla TEXT,
    tipo_operacao TEXT NOT NULL DEFAULT 'convenio'
        CHECK (tipo_operacao IN ('convenio', 'agendamento')),
    ativo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(id_convenio)
);

CREATE TABLE IF NOT EXISTS public.integrador_operacoes (
    id SERIAL PRIMARY KEY,
    id_integrador INT NOT NULL REFERENCES public.integradores(id_integrador) ON DELETE CASCADE,
    id_convenio INT NOT NULL REFERENCES public.convenios(id_convenio) ON DELETE CASCADE,
    rotina TEXT NOT NULL,
    descricao TEXT,
    tipo_processamento TEXT NOT NULL DEFAULT 'local'
        CHECK (tipo_processamento IN ('local', 'server', 'remoto')),
    ativo BOOLEAN DEFAULT TRUE,
    ordem INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(id_integrador, rotina)
);

-- Seed de integradores
INSERT INTO public.integradores (id_convenio, nome, sigla, tipo_operacao) VALUES
    (1,   'Bradesco Saúde',        'BRADESCO',     'convenio'),
    (2,   'Unimed Anápolis',       'UNIMED_ANA',   'convenio'),
    (3,   'Unimed Goiânia',        'UNIMED_GOI',   'convenio'),
    (6,   'IPASGO',                'IPASGO',       'convenio'),
    (8,   'SulAmérica',            'SULAMERICA',   'convenio'),
    (9,   'Amil',                  'AMIL',         'convenio'),
    (21,  'Unimed Intercâmbio',    'UNIMED_INT',   'convenio'),
    (31,  'IPASGO Geral',          'IPASGO_GER',   'convenio'),
    (100, 'Evoluir',               'EVOLUIR',      'agendamento'),
    (101, 'ABA CLMF',              'ABA_CLMF',     'agendamento')
ON CONFLICT (id_convenio) DO NOTHING;

-- Seed de integrador_operacoes a partir de convenio_operacoes
INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento)
SELECT i.id_integrador, co.id_convenio, co.valor, co.descricao, 'local'
FROM public.convenio_operacoes co
JOIN public.integradores i ON i.id_convenio = co.id_convenio
ON CONFLICT (id_integrador, rotina) DO NOTHING;
