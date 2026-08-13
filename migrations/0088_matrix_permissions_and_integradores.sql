-- Migration 0088: Matrix Permissions, Client Integradores and Multi-Tenant Isolation
-- Author: Antigravity AI
-- Date: 2026-08-12

-- 1. Create table for Client-Integrador enablement (Admin assigns Integradores to User Clients)
CREATE TABLE IF NOT EXISTS public.user_integradores (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    id_integrador INT NOT NULL REFERENCES public.integradores(id_integrador) ON DELETE CASCADE,
    ativo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_user_integrador UNIQUE(user_id, id_integrador)
);

-- 2. Add permissoes column (JSONB) to public.users for granular action matrix
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS permissoes JSONB DEFAULT '{}'::jsonb;

-- 3. Ensure public.integrador_operacoes table has decoupled worker reference
ALTER TABLE public.integrador_operacoes ADD COLUMN IF NOT EXISTS id_integrador_worker INT;

-- Index for fast lookup of enabled integradores per user
CREATE INDEX IF NOT EXISTS idx_user_integradores_user_id ON public.user_integradores(user_id);
