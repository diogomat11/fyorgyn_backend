-- ============================================================
-- Migration 0103: Catálogo canônico de operações + backfill User↔Integrador
-- Plano: docs/plano_integradores_agendamento.md (D1/D6 / DEV-01)
-- Depende de: 0102 (realinhamento dos espaços de ID)
--
-- 1) operacao_canonica em worker.integrador_operacoes (fonte de verdade de runtime)
--    e em public.integrador_operacoes (espelho admin);
-- 2) Registra no catálogo worker as rotinas do Evoluir (100) usadas em produção
--    (standalone jobs.py:451-454 e chains guias_sync_service) porém ausentes;
-- 3) Seeds canônicos 101 (6 OPs) e 100 (5 OPs) — ver matriz no plano §5.2;
-- 4) Backfill de user_integradores a partir de user_convenios (sem quebra de tenants).
--
-- Idempotente: IF NOT EXISTS / NOT EXISTS / ON CONFLICT / guards operacao_canonica IS NULL.
-- ============================================================

-- ── 1. Colunas e índices únicos (uma rotina física por OP canônica por integrador) ──
ALTER TABLE worker.integrador_operacoes ADD COLUMN IF NOT EXISTS operacao_canonica TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_worker_integrador_op_canonica
    ON worker.integrador_operacoes (id_integrador, operacao_canonica)
    WHERE operacao_canonica IS NOT NULL;

ALTER TABLE public.integrador_operacoes ADD COLUMN IF NOT EXISTS operacao_canonica TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pub_integrador_op_canonica
    ON public.integrador_operacoes (id_integrador, operacao_canonica)
    WHERE operacao_canonica IS NOT NULL;

-- ── 2. Registrar rotinas do Evoluir (100) ausentes no catálogo worker ──
--    (espelha o que já existe em public.integrador_operacoes; mantém a validação
--     da DEV-08 sem quebrar os fluxos standalone/chain em produção)
INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
SELECT 100, v.rotina, v.descricao, 'local', TRUE, 'automatico'
FROM (VALUES
    ('op2_obterDetalhes',    'Consulta de detalhes do paciente no portal Evoluir (chain automática)'),
    ('op4_atualizarDataPTS', 'Atualização da data do PTS no portal Evoluir'),
    ('op5_ImportCorpoClinico', 'Importação do corpo clínico do portal Evoluir'),
    ('op6_baixarFaturados',  'Download da listagem de faturados do portal Evoluir'),
    ('op7_consultaDocs',     'Consulta de documentos do portal Evoluir'),
    ('OP_consultaDocs',      'Alias legado de op7_consultaDocs (compatibilidade standalone)')
) AS v(rotina, descricao)
WHERE NOT EXISTS (
    SELECT 1 FROM worker.integrador_operacoes w
    WHERE w.id_integrador = 100 AND w.rotina = v.rotina
);

-- ── 3. Seeds canônicos — schema worker (id_integrador no ESPAÇO W) ──
UPDATE worker.integrador_operacoes SET operacao_canonica = 'sincronizar_agendamentos'
WHERE id_integrador = 101 AND rotina = 'op1_importar_agendamentos' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'consultar_carteirinha'
WHERE id_integrador = 101 AND rotina = 'op2_consultar_carteirinha' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'confirmar_agendamento'
WHERE id_integrador = 101 AND rotina = 'op3_confirmar_agendamento' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'registrar_falta'
WHERE id_integrador = 101 AND rotina = 'op4_registrar_falta' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'remover_falta'
WHERE id_integrador = 101 AND rotina = 'op5_remover_falta' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'atualizar_rc'
WHERE id_integrador = 101 AND rotina = 'op6_atualizar_rc' AND operacao_canonica IS NULL;

UPDATE worker.integrador_operacoes SET operacao_canonica = 'importar_pacientes'
WHERE id_integrador = 100 AND rotina = 'op1_importPacientes' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'atualizar_data_pts'
WHERE id_integrador = 100 AND rotina = 'op4_atualizarDataPTS' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'importar_corpo_clinico'
WHERE id_integrador = 100 AND rotina = 'op5_ImportCorpoClinico' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'baixar_faturados'
WHERE id_integrador = 100 AND rotina = 'op6_baixarFaturados' AND operacao_canonica IS NULL;
UPDATE worker.integrador_operacoes SET operacao_canonica = 'consultar_documentos'
WHERE id_integrador = 100 AND rotina = 'op7_consultaDocs' AND operacao_canonica IS NULL;

-- ── 3b. Espelho — schema public (chave pelo id_convenio, já realinhado pela 0102) ──
UPDATE public.integrador_operacoes SET operacao_canonica = 'sincronizar_agendamentos'
WHERE id_convenio = 101 AND rotina = 'op1_importar_agendamentos' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'consultar_carteirinha'
WHERE id_convenio = 101 AND rotina = 'op2_consultar_carteirinha' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'confirmar_agendamento'
WHERE id_convenio = 101 AND rotina = 'op3_confirmar_agendamento' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'registrar_falta'
WHERE id_convenio = 101 AND rotina = 'op4_registrar_falta' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'remover_falta'
WHERE id_convenio = 101 AND rotina = 'op5_remover_falta' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'atualizar_rc'
WHERE id_convenio = 101 AND rotina = 'op6_atualizar_rc' AND operacao_canonica IS NULL;

UPDATE public.integrador_operacoes SET operacao_canonica = 'importar_pacientes'
WHERE id_convenio = 100 AND rotina = 'op1_importPacientes' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'atualizar_data_pts'
WHERE id_convenio = 100 AND rotina = 'op4_atualizarDataPTS' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'importar_corpo_clinico'
WHERE id_convenio = 100 AND rotina = 'op5_ImportCorpoClinico' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'baixar_faturados'
WHERE id_convenio = 100 AND rotina = 'op6_baixarFaturados' AND operacao_canonica IS NULL;
UPDATE public.integrador_operacoes SET operacao_canonica = 'consultar_documentos'
WHERE id_convenio = 100 AND rotina IN ('op7_consultaDocs', 'OP_consultaDocs') AND operacao_canonica IS NULL;

-- ── 4. Backfill de vínculos: quem já opera integrador de agendamento via
--       user_convenios passa a ter o vínculo user_integradores (espaço W) ──
INSERT INTO public.user_integradores (user_id, id_integrador, ativo)
SELECT DISTINCT uc.user_id, c.id_integrador, TRUE
FROM public.user_convenios uc
JOIN public.convenios c ON c.id_convenio = uc.id_convenio
JOIN worker.integradores wi ON wi.id_integrador = c.id_integrador
                           AND wi.tipo_operacao = 'agendamento'
                           AND wi.ativo = TRUE
ON CONFLICT (user_id, id_integrador) DO NOTHING;
