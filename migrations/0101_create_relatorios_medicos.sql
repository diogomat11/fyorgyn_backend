-- Migration 0101: Cria tabela relatorios_medicos e adiciona is_active em relatorios_clinicos
-- Relacionamentos:
--   relatorios_medicos.user_id       -> users.id
--   relatorios_medicos.id_paciente   -> logico via carteirinhas.id_paciente
--   relatorios_medicos.id_relatorio  -> relatorios_medicos_extracao.id_relatorio (logico)

CREATE TABLE IF NOT EXISTS public.relatorios_medicos (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    id_paciente      TEXT    NOT NULL,
    nome_paciente    TEXT,
    id_relatorio     TEXT,
    url_arquivo      TEXT,
    data_relatorio   DATE,
    nome_medico      TEXT,
    especialidade    TEXT,
    observacoes      TEXT,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Constraint única por paciente/relatório para evitar duplicações
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_relatorio_medico_pac_rel'
    ) THEN
        ALTER TABLE public.relatorios_medicos 
        ADD CONSTRAINT uq_relatorio_medico_pac_rel UNIQUE (user_id, id_paciente, id_relatorio);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_relatorios_medicos_user     ON public.relatorios_medicos (user_id);
CREATE INDEX IF NOT EXISTS idx_relatorios_medicos_pac      ON public.relatorios_medicos (id_paciente);
CREATE INDEX IF NOT EXISTS idx_relatorios_medicos_rel      ON public.relatorios_medicos (id_relatorio);
CREATE INDEX IF NOT EXISTS idx_relatorios_medicos_active   ON public.relatorios_medicos (is_active);

-- Adiciona is_active em relatorios_clinicos (PTS e ANEXO-II)
ALTER TABLE public.relatorios_clinicos
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_relatorios_clinicos_active ON public.relatorios_clinicos (is_active);

COMMENT ON TABLE public.relatorios_medicos IS
  'Documentos de Pedido/Relatório Médico (RM) por paciente. id_relatorio referencia logicamente relatorios_medicos_extracao.id_relatorio.';

COMMENT ON COLUMN public.relatorios_medicos.id_relatorio IS
  'Referência lógica a relatorios_medicos_extracao.id_relatorio. NULL quando o arquivo foi inserido manualmente sem extração AI.';
