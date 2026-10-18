-- ============================================================
-- Migration 0107: Adicionar coluna abrev em areas_atuacao
--
-- Coluna de abreviação para identificação rápida da área de
-- atuação em exports e relatórios.
-- ============================================================

ALTER TABLE public.areas_atuacao ADD COLUMN IF NOT EXISTS abrev TEXT;

UPDATE public.areas_atuacao SET abrev = 'PSI'    WHERE UPPER(TRIM(nome)) = 'PSICOLOGIA';
UPDATE public.areas_atuacao SET abrev = 'FONO'   WHERE UPPER(TRIM(nome)) = 'FONOAUDIOLOGIA';
UPDATE public.areas_atuacao SET abrev = 'TO'     WHERE UPPER(TRIM(nome)) = 'TERAPIA OCUPACIONAL';
UPDATE public.areas_atuacao SET abrev = 'MUSICO' WHERE UPPER(TRIM(nome)) = 'MUSICOTERAPIA';
UPDATE public.areas_atuacao SET abrev = 'PED'    WHERE UPPER(TRIM(nome)) = 'PSICOPEDAGOGIA';
UPDATE public.areas_atuacao SET abrev = 'MOTRIC' WHERE UPPER(TRIM(nome)) = 'PSICOMOTRICIDADE';
UPDATE public.areas_atuacao SET abrev = 'FISIO'  WHERE UPPER(TRIM(nome)) = 'FISIOTERAPIA';
