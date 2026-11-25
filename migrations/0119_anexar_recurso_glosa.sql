-- 0119 — OP21: Anexar Arquivo ao Recurso de Glosa (IPASGO)
--
-- Upload de PDF e vínculo ao protocolo do recurso de glosa no novo portal:
--   1. POST /Upload/SendFile (multipart) -> PathFileName temporário no servidor
--   2. POST /RecursoGlosa/SaveFile { param: base64({NumeroProtocolo, PathFileName, FileName}) }
--   3. GET  /RecursoGlosa/GetAnexos?chaveCapa={protocolo} (validação e listagem)
-- Sem DDL novo — apenas seeds nos catálogos de operações.

INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES (6, 'op21_anexar_recurso_glosa',
        'Anexar arquivo PDF ao protocolo no módulo RecursoGlosa do IPASGO',
        'local', TRUE, 'automatico')
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES (6, 6, 'op21_anexar_recurso_glosa',
        'Anexar arquivo PDF ao protocolo no módulo RecursoGlosa do IPASGO',
        'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.convenio_operacoes (id_convenio, valor, descricao)
VALUES (6, 'op21_anexar_recurso_glosa', '21 - Anexar Arquivo ao Recurso de Glosa (IPASGO)')
ON CONFLICT DO NOTHING;
