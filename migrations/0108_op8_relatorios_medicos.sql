-- ============================================================
-- Migration 0108: OP8 Evoluir — Importar Relatórios Médicos
--
-- 1) Coluna data_validade em public.relatorios_medicos
--    (a página painel/relatorios/relatorio-medico traz data do
--     documento E data de validade);
-- 2) Registro da rotina op8_importRelatoriosMedicos nos catálogos:
--    - worker.integrador_operacoes (fonte de verdade do runtime —
--      o dispatcher BLOQUEIA rotina não catalogada, ver
--      dispatcher._rotina_ativa_integrador);
--    - public.integrador_operacoes (espelho admin);
--    - public.convenio_operacoes (alimenta o seletor de rotinas
--      do Importações).
--
-- Idempotente: ADD COLUMN IF NOT EXISTS / ON CONFLICT / NOT EXISTS.
-- ============================================================

-- ── 1. Coluna data_validade ──
ALTER TABLE public.relatorios_medicos
    ADD COLUMN IF NOT EXISTS data_validade DATE;

-- ── 2. Catálogo worker (runtime do dispatcher) ──
ALTER TABLE worker.integrador_operacoes
    ADD COLUMN IF NOT EXISTS operacao_canonica TEXT;

INSERT INTO worker.integrador_operacoes
    (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao, operacao_canonica)
VALUES
    (100, 'op8_importRelatoriosMedicos',
     'Importação de relatórios médicos do portal Evoluir (por paciente ou todos)',
     'local', TRUE, 'automatico', 'importar_relatorios_medicos')
ON CONFLICT DO NOTHING;

UPDATE worker.integrador_operacoes
SET operacao_canonica = 'importar_relatorios_medicos'
WHERE id_integrador = 100
  AND rotina = 'op8_importRelatoriosMedicos'
  AND operacao_canonica IS NULL;

-- ── 3. Espelho admin (public) ──
INSERT INTO public.integrador_operacoes
    (id_integrador, id_convenio, rotina, operacao_canonica, descricao, tipo_processamento, ativo)
SELECT i.id_integrador, i.id_convenio, 'op8_importRelatoriosMedicos',
       'importar_relatorios_medicos',
       'Importação de relatórios médicos do portal Evoluir (por paciente ou todos)',
       'local', TRUE
FROM public.integradores i
WHERE i.id_convenio = 100
ON CONFLICT (id_integrador, rotina) DO NOTHING;

-- ── 4. Seletor de rotinas do Importações ──
INSERT INTO public.convenio_operacoes (id_convenio, descricao, valor)
SELECT 100, 'OP=8 (Importar Relatórios Médicos)', 'op8_importRelatoriosMedicos'
WHERE NOT EXISTS (
    SELECT 1 FROM public.convenio_operacoes
    WHERE id_convenio = 100 AND valor = 'op8_importRelatoriosMedicos'
);
