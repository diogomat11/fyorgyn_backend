-- ============================================================
-- Migration 0102: Realinha public.integradores ao espaço W (id_integrador = id_convenio)
-- Plano: docs/plano_integradores_agendamento.md (D8 / DEV-00→DEV-01)
--
-- Contexto (auditoria DEV-00 de 2026-08-25): public.integradores usa SERIAL da 0085
-- (6→4, 8→5, 9→6, 21→7, 31→8, 100→9, 101→10) enquanto TODO o runtime opera no espaço
-- do worker (worker.integradores: 1,2,3,6,100,101). O admin grava ids do espaço W em
-- user_integradores (via GET /api/integradores), que só satisfazem a FK após este
-- realinhamento. user_integradores está VAZIA na data (auditada) — sem dependentes.
--
-- Idempotente: re-execução não altera nada (guards em todos os UPDATEs; FKs drop+add).
-- ============================================================

-- 1. Remover temporariamente as FKs que apontam para public.integradores
ALTER TABLE public.integrador_operacoes DROP CONSTRAINT IF EXISTS integrador_operacoes_id_integrador_fkey;
ALTER TABLE public.user_integradores DROP CONSTRAINT IF EXISTS user_integradores_id_integrador_fkey;

-- 2. Remapear dependentes ANTES do swap do PK
--    integrador_operacoes tem id_convenio na própria linha (mirror per-convenio da 0085).
--    Duas passadas com negativo temporário: evita colisão transiente do
--    UNIQUE(id_integrador, rotina) durante o UPDATE (ex.: ops do Amil ainda em id 6
--    enquanto as do IPASGO migram para 6).
UPDATE public.integrador_operacoes
SET id_integrador = -id_convenio
WHERE id_integrador <> id_convenio;

UPDATE public.integrador_operacoes
SET id_integrador = -id_integrador
WHERE id_integrador < 0;

--    user_integradores: id novo = id_convenio do integrador referenciado (espaço W)
UPDATE public.user_integradores ui
SET id_integrador = i.id_convenio
FROM public.integradores i
WHERE ui.id_integrador = i.id_integrador
  AND ui.id_integrador <> i.id_convenio;

-- 3. Swap do PK em duas passadas (negativo temporário evita colisão de unique)
UPDATE public.integradores SET id_integrador = -id_convenio WHERE id_integrador <> id_convenio;
UPDATE public.integradores SET id_integrador = -id_integrador WHERE id_integrador < 0;

-- 4. Recriar FKs (agora consistentes com o espaço W)
ALTER TABLE public.integrador_operacoes
    ADD CONSTRAINT integrador_operacoes_id_integrador_fkey
    FOREIGN KEY (id_integrador) REFERENCES public.integradores(id_integrador) ON DELETE CASCADE;

ALTER TABLE public.user_integradores
    ADD CONSTRAINT user_integradores_id_integrador_fkey
    FOREIGN KEY (id_integrador) REFERENCES public.integradores(id_integrador) ON DELETE CASCADE;

-- 5. Avançar a sequence para além do maior id (101) — próximos inserts do admin
SELECT setval('public.integradores_id_integrador_seq',
              (SELECT COALESCE(MAX(id_integrador), 1) FROM public.integradores));
