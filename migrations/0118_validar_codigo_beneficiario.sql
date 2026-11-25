-- 0118 — OP20: Validar/Extrair CodigoBeneficiario (IPASGO)
--
-- 1. Colunas de rastreio em public.carteirinhas:
--    - codigo_benef_validado_em TIMESTAMPTZ NULL
--    - codigo_benef_status TEXT NULL
-- 2. Tabela de auditoria/relatório multi-tenant por user_id:
--    - public.carteirinha_codigo_benef_auditoria
-- 3. Seeds da rotina op20_validar_codigo_beneficiario nos 3 catálogos (id_integrador=6, id_convenio=6).

ALTER TABLE public.carteirinhas
    ADD COLUMN IF NOT EXISTS codigo_benef_validado_em TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS codigo_benef_status TEXT NULL;

CREATE TABLE IF NOT EXISTS public.carteirinha_codigo_benef_auditoria (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    carteirinha_id INTEGER NULL REFERENCES public.carteirinhas(id) ON DELETE SET NULL,
    carteirinha TEXT NULL,
    paciente TEXT NULL,
    codigo_anterior TEXT NULL,
    codigo_portal TEXT NULL,
    titulo TEXT NULL,
    opcoes JSONB NULL,
    fonte TEXT NULL,
    status TEXT NOT NULL,
    aplicado BOOLEAN NOT NULL DEFAULT FALSE,
    job_id INTEGER NULL,
    erro TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cart_cod_benef_audit_user_created
    ON public.carteirinha_codigo_benef_auditoria (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cart_cod_benef_audit_cart
    ON public.carteirinha_codigo_benef_auditoria (carteirinha_id);

INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES (6, 'op20_validar_codigo_beneficiario',
        'Validar e extrair CodigoBeneficiario no portal IPASGO por carteirinha',
        'local', TRUE, 'automatico')
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES (6, 6, 'op20_validar_codigo_beneficiario',
        'Validar e extrair CodigoBeneficiario no portal IPASGO por carteirinha',
        'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO UPDATE SET
    descricao = EXCLUDED.descricao,
    ativo = EXCLUDED.ativo;

INSERT INTO public.convenio_operacoes (id_convenio, valor, descricao)
VALUES (6, 'op20_validar_codigo_beneficiario', '20 - Validar Código Beneficiário (Carteirinhas)')
ON CONFLICT DO NOTHING;
