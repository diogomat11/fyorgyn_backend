-- Migration: 0077_create_user_convenio_workflows.sql
-- Description: Cria tabela user_convenio_workflows para sequenciamento customizado de nós de workflow por usuario e convenio

CREATE TABLE IF NOT EXISTS public.user_convenio_workflows (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    id_convenio INTEGER NOT NULL REFERENCES public.convenios(id_convenio) ON DELETE CASCADE,
    nome_workflow TEXT NOT NULL,
    fluxo_passos JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_convenio_workflow UNIQUE (user_id, id_convenio)
);
