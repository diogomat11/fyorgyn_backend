-- 0114 — Recurso de Glosa IPASGO (OP10 importação + OP16 envio de justificativas)
--
-- 1) lotes_convenio.protocolo (TEXT): protocolo/capa do lote no portal FacPlan
--    (endpoint /RecursoGlosa). Parâmetro do job da OP10 junto com cod_prestador.
-- 2) justificativas_glosa: catálogo de justificativas por tenant (padrão
--    motivos_faltas) — texto digitado pelo usuário, reaproveitável em vários itens.
-- 3) recurso_glosa_itens: itens de glosa trazidos pela OP10
--    (PesquisarServicosLote), 1 linha por (lote + Chave). Relaciona:
--    - codigo_segurado -> carteirinhas.codigo_beneficiario (carteirinha_id);
--    - guia (sem zeros à esquerda) -> base_guias (base_guia_id);
--    - fatura -> faturamento_lotes (id_faturamento_lote, por lote+guia+procedimento),
--      que por sua vez referencia lotes_convenio.id_lote.
-- 4) Seeds das OP10 (atualiza descrição) e OP16 (nova) nos 3 catálogos
--    (padrão 0111): worker.integrador_operacoes, public.integrador_operacoes,
--    public.convenio_operacoes.

-- ── 1. Protocolo do lote no portal ──
ALTER TABLE lotes_convenio
    ADD COLUMN IF NOT EXISTS protocolo TEXT;

COMMENT ON COLUMN lotes_convenio.protocolo IS
    'Protocolo/capa do lote no portal FacPlan (módulo RecursoGlosa). Parâmetro das OPs 10 (importar) e 16 (enviar justificativas).';

-- ── 2. Catálogo de justificativas de glosa ──
CREATE TABLE IF NOT EXISTS justificativas_glosa (
    id SERIAL PRIMARY KEY,
    descricao TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT DEFAULT 'Ativo',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_justificativas_glosa_user_status
    ON justificativas_glosa (user_id, status);

-- ── 3. Itens de recurso de glosa ──
CREATE TABLE IF NOT EXISTS recurso_glosa_itens (
    id SERIAL PRIMARY KEY,

    id_lote INTEGER NOT NULL REFERENCES lotes_convenio(id_lote) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    protocolo TEXT,

    -- Identificadores do portal (payload do envio na OP16)
    chave BIGINT NOT NULL,
    chave_ut_serv BIGINT,
    quantidade_glosada INTEGER,
    valor_unitario_glosado DOUBLE PRECISION,
    valor_recurso DOUBLE PRECISION,

    -- Segurado/beneficiário
    codigo_segurado TEXT,
    nome_segurado TEXT,
    carteirinha_id INTEGER REFERENCES carteirinhas(id) ON DELETE SET NULL,

    -- Guia e procedimento (guia SEM zeros à esquerda; codigo_amb SEM '.' e '-')
    guia TEXT,
    base_guia_id INTEGER REFERENCES base_guias(id) ON DELETE SET NULL,
    codigo_amb TEXT,
    descricao_servico TEXT,

    -- Fatura correspondente no lote (filtro por data via dataRealizacao)
    id_faturamento_lote INTEGER REFERENCES faturamento_lotes(id) ON DELETE SET NULL,

    -- Justificativas: texto atual no portal x texto aplicado pelo usuário
    justificativa_portal TEXT,
    justificativa_id INTEGER REFERENCES justificativas_glosa(id) ON DELETE SET NULL,
    justificativa_texto TEXT,

    -- Pendente | Justificativa Definida | Enviado
    status TEXT DEFAULT 'Pendente',
    data_envio TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_recurso_glosa_lote_chave UNIQUE (id_lote, chave)
);

CREATE INDEX IF NOT EXISTS idx_recurso_glosa_itens_lote_status
    ON recurso_glosa_itens (id_lote, status);
CREATE INDEX IF NOT EXISTS idx_recurso_glosa_itens_guia
    ON recurso_glosa_itens (guia);
CREATE INDEX IF NOT EXISTS idx_recurso_glosa_itens_codigo_segurado
    ON recurso_glosa_itens (codigo_segurado);
CREATE INDEX IF NOT EXISTS idx_recurso_glosa_itens_carteirinha
    ON recurso_glosa_itens (carteirinha_id);
CREATE INDEX IF NOT EXISTS idx_recurso_glosa_itens_user
    ON recurso_glosa_itens (user_id);

COMMENT ON COLUMN recurso_glosa_itens.chave IS 'Chave do serviço no portal (payload Chave da OP16); única por lote.';
COMMENT ON COLUMN recurso_glosa_itens.guia IS 'Número da guia SEM zeros à esquerda (ex.: 024533543 -> 24533543).';
COMMENT ON COLUMN recurso_glosa_itens.codigo_amb IS 'Código do procedimento normalizado, sem pontos e hífens (ex.: 5.00.01.22-1 -> 50001221).';
COMMENT ON COLUMN recurso_glosa_itens.justificativa_portal IS 'Justificativa atual retornada pelo portal (referência); não sobrescreve a do usuário em re-importações.';
COMMENT ON COLUMN recurso_glosa_itens.justificativa_texto IS 'Texto efetivo aplicado pelo usuário (do catálogo justificativas_glosa ou edição inline).';

-- ── 4. Seeds das OPs nos catálogos ──

-- Catálogo runtime do worker (IPASGO = id_integrador 6)
INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES
    (6, 'op10_recurso_glosa',
     'Importar itens de recurso de glosa de um lote/protocolo do IPASGO (FacPlan)',
     'local', TRUE, 'automatico'),
    (6, 'op16_enviar_recurso_glosa',
     'Envio de justificativas de recurso de glosa ao IPASGO (SalvarDigitacaoParcialmente)',
     'local', TRUE, 'automatico')
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

-- Espelho admin (public)
INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES
    (6, 6, 'op10_recurso_glosa',
     'Importar itens de recurso de glosa de um lote/protocolo do IPASGO (FacPlan)',
     'local', TRUE),
    (6, 6, 'op16_enviar_recurso_glosa',
     'Envio de justificativas de recurso de glosa ao IPASGO (SalvarDigitacaoParcialmente)',
     'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

-- Seletor público de operações (padrão pós-0064: valor = nome do arquivo)
INSERT INTO public.convenio_operacoes (id_convenio, valor, descricao)
VALUES
    (6, 'op10_recurso_glosa', '10 - Importar Recurso de Glosa (IPASGO)'),
    (6, 'op16_enviar_recurso_glosa', '16 - Enviar Justificativas Recurso de Glosa (IPASGO)')
ON CONFLICT DO NOTHING;

UPDATE public.convenio_operacoes
SET descricao = '10 - Importar Recurso de Glosa (IPASGO)'
WHERE id_convenio = 6 AND valor = 'op10_recurso_glosa'
  AND descricao <> '10 - Importar Recurso de Glosa (IPASGO)';
