-- Migration 0068: Criar tabela solicitacoes e migrar guias pendentes/canceladas/negadas

CREATE TABLE IF NOT EXISTS public.solicitacoes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES public.users(id) ON DELETE SET NULL,
    carteirinha_id INTEGER REFERENCES public.carteirinhas(id) ON DELETE CASCADE,
    id_convenio INTEGER REFERENCES public.convenios(id_convenio) ON DELETE SET NULL,
    
    -- Dados da guia
    guia TEXT,
    codigo_terapia TEXT,
    nome_terapia TEXT,
    qtde_solicitada INTEGER DEFAULT 0,
    sessoes_autorizadas INTEGER DEFAULT 0,
    data_solicitacao DATE,
    data_autorizacao DATE,
    senha TEXT,
    validade DATE,
    status_solicitacao TEXT DEFAULT 'Pendente',
    
    -- Dados do formulário
    id_profissional TEXT,
    id_medico TEXT,
    observacao TEXT,
    paciente_CID TEXT,
    
    -- Anexos
    anexo_RM TEXT,
    anexo_AI TEXT,
    anexo_RC TEXT,
    
    job_id INTEGER,
    base_guia_id INTEGER REFERENCES public.base_guias(id) ON DELETE SET NULL,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Adicionar FK de job_id com schema worker
ALTER TABLE public.solicitacoes ADD CONSTRAINT fk_sol_job FOREIGN KEY (job_id) REFERENCES worker.jobs(id) ON DELETE SET NULL;

-- Constraint de unicidade
ALTER TABLE public.solicitacoes ADD CONSTRAINT uq_solicitacao_guia_terapia UNIQUE (guia, id_convenio, codigo_terapia, carteirinha_id, user_id);

CREATE INDEX IF NOT EXISTS idx_solicitacoes_user_id ON public.solicitacoes(user_id);
CREATE INDEX IF NOT EXISTS idx_solicitacoes_status ON public.solicitacoes(status_solicitacao);
CREATE INDEX IF NOT EXISTS idx_solicitacoes_job_id ON public.solicitacoes(job_id);

-- Copiar registros não-autorizados de base_guias para solicitacoes
INSERT INTO public.solicitacoes (
    user_id, carteirinha_id, id_convenio, guia, codigo_terapia, nome_terapia, 
    qtde_solicitada, sessoes_autorizadas, data_solicitacao, data_autorizacao, 
    senha, validade, status_solicitacao, created_at, updated_at
)
SELECT 
    user_id, carteirinha_id, id_convenio, guia, codigo_terapia, nome_terapia, 
    qtde_solicitada, sessoes_autorizadas, data_solicitacao, data_autorizacao, 
    senha, validade, status_guia, created_at, updated_at
FROM public.base_guias
WHERE 
    -- Para IPASGO (id_convenio = 6), mantemos apenas 'Autorizado' e 'Parcialmente autorizada' (e variações com 'autorizad')
    (id_convenio = 6 AND LOWER(status_guia) NOT LIKE '%autorizad%')
    OR
    -- Para outros convênios, mantemos apenas os que contêm 'autorizad' ou 'liberad'
    (id_convenio != 6 AND LOWER(status_guia) NOT LIKE '%autorizad%' AND LOWER(status_guia) NOT LIKE '%liberad%')
ON CONFLICT (guia, id_convenio, codigo_terapia, carteirinha_id, user_id) DO NOTHING;

-- Deletar os registros migrados de base_guias
DELETE FROM public.base_guias
WHERE 
    (id_convenio = 6 AND LOWER(status_guia) NOT LIKE '%autorizad%')
    OR
    (id_convenio != 6 AND LOWER(status_guia) NOT LIKE '%autorizad%' AND LOWER(status_guia) NOT LIKE '%liberad%');
