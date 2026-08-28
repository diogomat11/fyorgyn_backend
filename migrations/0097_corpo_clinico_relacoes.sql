-- ============================================================
-- Migration 0097: Corpo Clinico - FK id_area + relacoes N:N
-- ============================================================
-- Description:
--   1. corpo_clinico.id_area: FK para areas_atuacao (substitui a relacao
--      TEXTUAL via coluna `area`, que e mantida por compatibilidade de
--      leitura/queries existentes).
--   2. Tabelas N:N de vinculo do profissional:
--      - corpo_clinico_areas        -> areas de atuacao do profissional
--      - corpo_clinico_convenios    -> convenios atendidos
--      - corpo_clinico_procedimentos-> procedimentos habilitados
--        (procedimentos ja relaciona com areas_atuacao e convenios - as N:N
--         permitem exceções/refinamento sobre o derivado areas ∩ convenios)
--      - corpo_clinico_users        -> um profissional pode vincular a mais
--        de um user_id (multi-tenant)
--   3. Backfill id_area a partir do texto de `area` (match case-insensitive,
--      trim) + seed das N:N de areas/users a partir dos dados existentes.
--
-- Notas:
--   - corpo_clinico.id e PK SERIAL desde a 0083.
--   - A coluna `area` (texto) NAO e removida: endpoints legados usam
--     (id_profissional, area) como chave de PUT/DELETE.

-- 1. FK id_area em corpo_clinico
ALTER TABLE public.corpo_clinico
ADD COLUMN IF NOT EXISTS id_area INTEGER REFERENCES public.areas_atuacao(id_area) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_corpo_clinico_id_area ON public.corpo_clinico(id_area);

-- 2. Tabelas N:N
CREATE TABLE IF NOT EXISTS public.corpo_clinico_areas (
    id SERIAL PRIMARY KEY,
    corpo_clinico_id INTEGER NOT NULL REFERENCES public.corpo_clinico(id) ON DELETE CASCADE,
    id_area INTEGER NOT NULL REFERENCES public.areas_atuacao(id_area) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (corpo_clinico_id, id_area)
);

CREATE TABLE IF NOT EXISTS public.corpo_clinico_convenios (
    id SERIAL PRIMARY KEY,
    corpo_clinico_id INTEGER NOT NULL REFERENCES public.corpo_clinico(id) ON DELETE CASCADE,
    id_convenio INTEGER NOT NULL REFERENCES public.convenios(id_convenio) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (corpo_clinico_id, id_convenio)
);

CREATE TABLE IF NOT EXISTS public.corpo_clinico_procedimentos (
    id SERIAL PRIMARY KEY,
    corpo_clinico_id INTEGER NOT NULL REFERENCES public.corpo_clinico(id) ON DELETE CASCADE,
    id_procedimento INTEGER NOT NULL REFERENCES public.procedimentos(id_procedimento) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (corpo_clinico_id, id_procedimento)
);

CREATE TABLE IF NOT EXISTS public.corpo_clinico_users (
    id SERIAL PRIMARY KEY,
    corpo_clinico_id INTEGER NOT NULL REFERENCES public.corpo_clinico(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (corpo_clinico_id, user_id)
);

-- 3. Backfill: id_area a partir do texto de `area` (case-insensitive + trim)
UPDATE public.corpo_clinico cc
SET id_area = aa.id_area
FROM public.areas_atuacao aa
WHERE cc.id_area IS NULL
  AND cc.area IS NOT NULL
  AND cc.area <> ''
  AND LOWER(TRIM(cc.area)) = LOWER(TRIM(aa.nome));

-- 3.1 Relatorio de nao-correspondencias (rodar e revisar manualmente;
--     profissionais com area textual fora do catalogo ficam id_area NULL)
-- SELECT cc.id, cc.nome, cc.area FROM public.corpo_clinico cc
-- WHERE cc.area IS NOT NULL AND cc.area <> '' AND cc.id_area IS NULL;

-- 4. Seed corpo_clinico_areas a partir das linhas com id_area preenchido
--    (cada linha historica de corpo_clinico = 1 area do profissional)
INSERT INTO public.corpo_clinico_areas (corpo_clinico_id, id_area)
SELECT cc.id, cc.id_area
FROM public.corpo_clinico cc
WHERE cc.id_area IS NOT NULL
ON CONFLICT (corpo_clinico_id, id_area) DO NOTHING;

-- 5. Seed corpo_clinico_users a partir do user_id historico
--    (o mesmo profissional pode ter varias linhas em corpo_clinico -> dedup via UNIQUE)
INSERT INTO public.corpo_clinico_users (corpo_clinico_id, user_id)
SELECT DISTINCT cc.id, cc.user_id
FROM public.corpo_clinico cc
WHERE cc.user_id IS NOT NULL
ON CONFLICT (corpo_clinico_id, user_id) DO NOTHING;

-- 6. Comentarios documentacionais
COMMENT ON COLUMN public.corpo_clinico.id_area IS 'FK areas_atuacao - fonte da verdade da area principal do profissional (coluna textual `area` mantida por compat). Vinculos adicionais em corpo_clinico_areas.';
COMMENT ON TABLE public.corpo_clinico_areas IS 'N:N profissional <-> areas de atuacao';
COMMENT ON TABLE public.corpo_clinico_convenios IS 'N:N profissional <-> convenios atendidos';
COMMENT ON TABLE public.corpo_clinico_procedimentos IS 'N:N profissional <-> procedimentos habilitados (refinamento sobre procedimentos das areas ∩ convenios do profissional)';
COMMENT ON TABLE public.corpo_clinico_users IS 'N:N profissional <-> users (um profissional pode pertencer a mais de um tenant/user_id)';
