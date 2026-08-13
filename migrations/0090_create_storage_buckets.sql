-- Migration 0090: Create Supabase Storage buckets for file uploads
-- Date: 2026-08-13
--
-- Buckets do Supabase Storage que substituem o filesystem local (uploads/).
-- Ver DEPLOY.md secao 3 (Fase 2).
--
-- Seguranca:
--   * Buckets PRIVADOS (public = false).
--   * O backend (Hub) acessa via SUPABASE_SERVICE_ROLE_KEY, que BYPASSA RLS
--     (service role tem acesso total). Por isso NAO sao necessarias politicas
--     (RLS policies) permissivas para o backend operar.
--   * O frontend NUNCA acessa o bucket diretamente: recebe "signed URLs"
--     geradas pelo backend (services/storage_service.py), que concedem GET
--     temporario a um objeto especifico.
--   * RLS segue habilitado em storage.objects (padrao Supabase): qualquer
--     acesso sem service role e negado por default (denegacao implicita).
--
-- Idempotente: ON CONFLICT DO NOTHING permite re-executar com seguranca
-- (caso algum bucket ja tenha sido criado via Dashboard).

-- Bucket para anexos de jobs (RM/AI/RC). Vida curta (~24h, cleanup automatico).
INSERT INTO storage.buckets (id, name, public)
VALUES ('anexos', 'anexos', false)
ON CONFLICT (id) DO NOTHING;

-- Buckets para o modulo Protocolo (PDFs de entrada e saida).
-- Ativacao prevista na Fase 2 (DEPLOY.md secao 3.3). Criados adiantadamente.
INSERT INTO storage.buckets (id, name, public)
VALUES ('protocolo-input', 'protocolo-input', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO storage.buckets (id, name, public)
VALUES ('protocolo-output', 'protocolo-output', false)
ON CONFLICT (id) DO NOTHING;
