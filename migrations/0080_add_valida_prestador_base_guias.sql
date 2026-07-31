-- Migration 0080: Adicionar coluna valida_prestador (JSONB) a public.base_guias
-- Description: Replica a coluna valida_prestador do projeto clmf_hub_basic para o
--              Agenda_hub_MultiConv. Armazena o JSON {tipo_json, guias} retornado
--              pelo worker (Unimed Goiania, id_convenio=3) apos a validacao do
--              vinculo do prestador via getErrosSapia.
--
-- Estrutura do JSON (fiel a valida_prestador_replication_prompt.yaml):
--   {
--     "tipo_json": "All Sucess" | "Thered" | "Null",
--     "guias": {
--       "<numero_guia>": {
--         "codigo_procedimento": "<codigo>",
--         "Vinculo_prestador": "Guia Valida" | "<mensagem_erro_unimed>"
--       }
--     }
--   }
--
-- Notas:
--   - Nullable: guias pendentes ou sem validacao terao NULL neste campo.
--   - JSONB (nao JSON) para permitir indexacao e queries eficientes no PostgreSQL.

ALTER TABLE public.base_guias
    ADD COLUMN IF NOT EXISTS valida_prestador JSONB;

COMMENT ON COLUMN public.base_guias.valida_prestador IS
    'JSON {tipo_json, guias} resultado da validacao de vinculo do prestador (Unimed Goiania via getErrosSapia). NULL quando nao validado.';
