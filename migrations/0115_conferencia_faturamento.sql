-- 0115 — Conferência de Faturamento IPASGO (OP17 importação + OP18 confirmação)
--
-- O portal substituiu o modal de faturamento (FaturamentoAtendimentos/LoadDetalhes
-- e ModificarDetalhe) pelo módulo ConferenciaFaturamento, endereçado por protocolo.
-- Novas OPs: op17_importar_conferencia (ObterProcedimentos) e
-- op18_conferir_faturamento (ConferirItem + EditarDataRealizacao).
--
-- 1) faturamento_lotes.status_conferencia (TEXT): letra do novo portal —
--    A=Aceito, P=Pendente, R=Recusado, I=Inapto (DataProcedimento vazio — não
--    habilitado a faturar). Espelho mantido na coluna Integer legada
--    StatusConferencia (A→67 Conferido, P→78 Não Conferido, R→82 Removido,
--    I→NULL) para telas/conciliações antigas.
-- 2) Seeds das OP17/OP18 nos 3 catálogos (padrão 0111/0114).
-- 3) Inativação das OP6/OP7 (endpoints antigos descontinuados pelo portal):
--    ativo=FALSE nos integrador_operacoes; DELETE no convenio_operacoes
--    (seletor público sem flag ativo). Arquivos/op_map permanecem (retrocompat).

ALTER TABLE faturamento_lotes
    ADD COLUMN IF NOT EXISTS status_conferencia TEXT;

COMMENT ON COLUMN faturamento_lotes.status_conferencia IS
    'Status do novo portal ConferenciaFaturamento: A=Aceito, P=Pendente, R=Recusado, I=Inapto (sem DataProcedimento — não habilitado a faturar). Espelhado em StatusConferencia (A→67, P→78, R→82, I→NULL).';

-- ── Seeds das OPs novas ──

INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES
    (6, 'op17_importar_conferencia',
     'Importar faturáveis do lote no novo módulo ConferenciaFaturamento do IPASGO (ObterProcedimentos)',
     'local', TRUE, 'automatico'),
    (6, 'op18_conferir_faturamento',
     'Confirmar item (ConferirItem) e enviar data de realização (EditarDataRealizacao) no ConferenciaFaturamento',
     'local', TRUE, 'automatico')
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES
    (6, 6, 'op17_importar_conferencia',
     'Importar faturáveis do lote no novo módulo ConferenciaFaturamento do IPASGO (ObterProcedimentos)',
     'local', TRUE),
    (6, 6, 'op18_conferir_faturamento',
     'Confirmar item (ConferirItem) e enviar data de realização (EditarDataRealizacao) no ConferenciaFaturamento',
     'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.convenio_operacoes (id_convenio, valor, descricao)
VALUES
    (6, 'op17_importar_conferencia', '17 - Importar Faturáveis (Conferência IPASGO)'),
    (6, 'op18_conferir_faturamento', '18 - Confirmar Item/Data (Conferência IPASGO)')
ON CONFLICT DO NOTHING;

-- ── Inativação das OP6/OP7 (endpoints antigos substituídos pelo portal) ──

UPDATE worker.integrador_operacoes
SET ativo = FALSE, descricao = descricao || ' [INATIVA: endpoint LoadDetalhes substituído pelo ConferenciaFaturamento]'
WHERE id_integrador = 6 AND rotina = 'op6_check_baixados' AND ativo IS DISTINCT FROM FALSE;

UPDATE worker.integrador_operacoes
SET ativo = FALSE, descricao = descricao || ' [INATIVA: endpoint ModificarDetalhe substituído pelo ConferenciaFaturamento]'
WHERE id_integrador = 6 AND rotina = 'op7_fat_facplan' AND ativo IS DISTINCT FROM FALSE;

UPDATE public.integrador_operacoes
SET ativo = FALSE
WHERE id_integrador = 6 AND rotina IN ('op6_check_baixados', 'op7_fat_facplan');

DELETE FROM public.convenio_operacoes
WHERE id_convenio = 6 AND valor IN ('op6_check_baixados', 'op7_fat_facplan');
