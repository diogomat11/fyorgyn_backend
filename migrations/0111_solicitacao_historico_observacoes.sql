-- 0111 — Histórico de observações do regulador IPASGO + OP15 EnviarJustificativa
--
-- 1) solicitacoes.historico_observacoes (JSONB): histórico de observações da
--    guia no regulador do IPASGO, trazido pela OP11 (GetHistoricoObservacao)
--    para guias não autorizadas e persistido pelo guias_sync_service.
--    Formato: [{data_hora, usuario, login, observacao, libera_pendente_doc,
--    libera_auditoria_operadora, libera_aguardando_beneficiario}].
-- 2) solicitacoes.guia_prestador (TEXT): número da guia do prestador, exigido
--    pelos endpoints de observação do regulador (AddHistoricoObservacao).
-- 3) Seeds da OP15:
--    - worker.integrador_operacoes: catálogo runtime (fonte de verdade);
--    - public.integrador_operacoes: espelho admin;
--    - public.convenio_operacoes: seletor de operações (valor = nome do arquivo
--      da OP, padrão pós-0064).

ALTER TABLE solicitacoes
    ADD COLUMN IF NOT EXISTS historico_observacoes JSONB;

ALTER TABLE solicitacoes
    ADD COLUMN IF NOT EXISTS guia_prestador TEXT;

-- Catálogo runtime do worker (IPASGO = id_integrador 6, espaço W legado)
INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES (6, 'op15_enviar_justificativa',
        'Envio de justificativa/observação para guia no regulador do IPASGO (FacPlan)',
        'local', TRUE, 'automatico')
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

-- Espelho admin (public)
INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES (6, 6, 'op15_enviar_justificativa',
        'Envio de justificativa/observação para guia no regulador do IPASGO (FacPlan)',
        'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

-- Seletor público de operações (padrão pós-0064: valor = nome do arquivo)
INSERT INTO public.convenio_operacoes (id_convenio, valor, descricao)
VALUES (6, 'op15_enviar_justificativa', '15 - Enviar Justificativa (Regulador IPASGO)')
ON CONFLICT DO NOTHING;
