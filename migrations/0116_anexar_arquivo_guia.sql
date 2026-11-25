-- 0116 — OP19: Anexar Arquivo à Guia (Conferência de Faturamento IPASGO)
--
-- Upload multipart (PDF) por guia do lote no novo portal:
--   POST /ConferenciaFaturamento/AnexarArquivoGuia?capaLoteId={protocolo}&numeroGuiaOperadora={guia}
-- O usuário fornece caminhos de PDFs nomeados ...<senha11>.pdf (guia = 8 primeiros
-- dígitos da senha); 1 job OP19 por arquivo. Sem DDL novo — apenas seeds.

INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES (6, 'op19_anexar_arquivo_guia',
        'Anexar arquivo PDF à guia do lote no módulo ConferenciaFaturamento do IPASGO',
        'local', TRUE, 'automatico')
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES (6, 6, 'op19_anexar_arquivo_guia',
        'Anexar arquivo PDF à guia do lote no módulo ConferenciaFaturamento do IPASGO',
        'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.convenio_operacoes (id_convenio, valor, descricao)
VALUES (6, 'op19_anexar_arquivo_guia', '19 - Anexar Arquivo à Guia (Conferência IPASGO)')
ON CONFLICT DO NOTHING;
