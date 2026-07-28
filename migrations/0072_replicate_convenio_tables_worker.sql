-- ============================================================
-- Migration 0072: Replicate Convenio and ConvenioOperacao tables in Worker schema
-- Creates worker.convenios and worker.convenio_operacoes to achieve full isolation
-- and populates them with initial supported convenios and routines.
-- ============================================================

CREATE TABLE IF NOT EXISTS worker.convenios (
    id SERIAL PRIMARY KEY,
    id_convenio INTEGER UNIQUE NOT NULL,
    nome TEXT NOT NULL,
    sigla TEXT,
    ativo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS worker.convenio_operacoes (
    id SERIAL PRIMARY KEY,
    id_convenio INTEGER NOT NULL REFERENCES worker.convenios(id_convenio) ON DELETE CASCADE,
    rotina TEXT NOT NULL,
    descricao TEXT,
    ativo BOOLEAN DEFAULT TRUE,
    params_schema JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(id_convenio, rotina)
);

-- Popula os convênios
INSERT INTO worker.convenios (id_convenio, nome, sigla, ativo) VALUES
(1, 'Bradesco Saúde', 'BRADESCO', TRUE),
(2, 'Unimed Anápolis', 'UNIMED_ANA', TRUE),
(3, 'Unimed Goiânia', 'UNIMED_GOI', TRUE),
(6, 'IPASGO', 'IPASGO', TRUE),
(100, 'Evoluir', 'EVOLUIR', TRUE)
ON CONFLICT (id_convenio) DO UPDATE SET
    nome = EXCLUDED.nome,
    sigla = EXCLUDED.sigla,
    ativo = EXCLUDED.ativo,
    updated_at = NOW();

-- Popula as operações de cada convênio
INSERT INTO worker.convenio_operacoes (id_convenio, rotina, descricao, ativo) VALUES
-- Bradesco (1)
(1, 'op1_consulta', 'Verificação de elegibilidade e consulta de guias', TRUE),

-- Unimed Anápolis (2)
(2, 'op1_consulta', 'Verificação de elegibilidade e consulta de guias', TRUE),
(2, 'op2_captura', 'Execução de captura facial/confirmação biométrica', TRUE),
(2, 'op3_execucao', 'Execução e faturamento de guias SADT no portal', TRUE),

-- Unimed Goiânia (3)
(3, 'op1_consulta', 'Verificação de elegibilidade e consulta de guias', TRUE),
(3, 'op2_captura', 'Execução de captura facial/confirmação biométrica', TRUE),
(3, 'op3_execucao', 'Execução e faturamento de guias SADT no portal', TRUE),

-- IPASGO (6)
(6, 'op1_autorizar_facplan', 'Solicitação de novas autorizações e anexação de laudos', TRUE),
(6, 'op3_import_guias', 'Importação visual de guias autorizadas via Selenium', TRUE),
(6, 'op4_confirma_guia', 'Liquidação/confirmação de sessões autorizadas', TRUE),
(6, 'op5_impress_guia', 'Impressão de guias via cliques Selenium', TRUE),
(6, 'op6_check_baixados', 'Leitura e conferência de faturas baixadas', TRUE),
(6, 'op7_fat_facplan', 'Faturamento/baixa de itens de guia com código 67', TRUE),
(6, 'op11_import_guias_api', 'Importação otimizada de guias autorizadas via API do FacPlan', TRUE),
(6, 'op12_impressao_api', 'Impressão rápida de guias via injeção HTTP', TRUE),
(6, 'op13_criar_lote', 'Solicitação de criação de novo lote de faturamento', TRUE),
(6, 'op13_poll_lote', 'Monitoramento e polling da geração do lote', TRUE),
(6, 'op14_cancelar_lote', 'Cancelamento de lotes pendentes ou gerados', TRUE),

-- Evoluir (100)
(100, 'op1_importPacientes', 'Importação total da listagem de pacientes do portal', TRUE),
(100, 'op3_ListarPTS', 'Consulta e extração de Projetos Terapêuticos Singulares (PTS)', TRUE)
ON CONFLICT (id_convenio, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;
