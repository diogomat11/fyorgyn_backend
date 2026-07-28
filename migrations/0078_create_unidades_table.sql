-- Migration 0078: Criar tabela public.unidades com suporte a multi-tenant e Seed Data

CREATE TABLE IF NOT EXISTS public.unidades (
    id SERIAL PRIMARY KEY,
    id_unidade INTEGER NOT NULL,
    nome VARCHAR(255) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'ativo',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_unidade_user_id UNIQUE (id_unidade, user_id)
);

-- Seed Data para user_id = 1 (ABA CLMF)
INSERT INTO public.unidades (id_unidade, nome, user_id, status)
VALUES
    (1, 'Unidade Oeste', 1, 'ativo'),
    (3, 'Unidade Externa', 1, 'ativo'),
    (5, 'Unidade República do Líbano', 1, 'ativo')
ON CONFLICT (id_unidade, user_id) DO UPDATE SET nome = EXCLUDED.nome;

-- Seed Data para user_id = 14 (Evoluir Matriz)
INSERT INTO public.unidades (id_unidade, nome, user_id, status)
VALUES
    (10, 'Matriz Evoluir', 14, 'ativo')
ON CONFLICT (id_unidade, user_id) DO UPDATE SET nome = EXCLUDED.nome;

-- Seed Data para user_id = 15 (Evoluir Nerópolis)
INSERT INTO public.unidades (id_unidade, nome, user_id, status)
VALUES
    (11, 'Matriz Nerópolis', 15, 'ativo')
ON CONFLICT (id_unidade, user_id) DO UPDATE SET nome = EXCLUDED.nome;
