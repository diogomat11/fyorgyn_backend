-- Migration 0084: corpo_clinico.id_profissional - remover default SERIAL e tornar nullable
-- Description: Corrige efeito colateral da migration 0083. A coluna id_profissional
--              foi originalmente criada como SERIAL/PK (NOT NULL com default nextval).
--              Agora que id (SERIAL) e a nova PK, id_profissional passa a ser apenas
--              um Text identificatorio opcional - medicos importados via CRM tem
--              id_profissional = NULL (vazio), conforme decisao do PO.
--
-- Acao:
--   1. Remover o default nextval de id_profissional (nao e mais SERIAL).
--   2. Tornar id_profissional nullable (medicos importados ficam NULL).
--   3. Dropar a sequencia agora-ociosa corpo_clinico_id_profissional_seq.
--
-- Referencias:
--   - Plano: Prompts_implantacoes/Contextos/2026_07_29_Plano_API_Consulta_CRM_Medico.md (RF-F3, decisao B)
--   - Correlata: migration 0083_corpo_clinico_id_seq_and_crm_columns.sql

-- 1. Remover default (DROP DEFAULT funciona mesmo se houver; IF EXISTS nao se aplica a ALTER COL DROP DEFAULT)
ALTER TABLE public.corpo_clinico ALTER COLUMN id_profissional DROP DEFAULT;

-- 2. Tornar nullable
ALTER TABLE public.corpo_clinico ALTER COLUMN id_profissional DROP NOT NULL;

-- 3. Dropar a sequencia ociosa (seguro: nada referencia apos DROP DEFAULT)
DROP SEQUENCE IF EXISTS public.corpo_clinico_id_profissional_seq;

COMMENT ON COLUMN public.corpo_clinico.id_profissional IS 'Identificador textual do profissional no portal de origem (UUID/numero). Medicos importados via CRM tem id_profissional=NULL - use a PK id.';
