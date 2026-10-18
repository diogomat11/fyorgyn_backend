-- ============================================================
-- Migration 0109: Integrador CAESAN (id_integrador=102) ↔ Convênio 16
--
-- Espaços de ID (ver docs/plano_caesan.md):
--   public.convenios.id_convenio = 16 (CAESAN) — vai no job, credenciais e UI
--   worker.integradores.id_integrador = 102 (sequência própria do espaço W:
--     1,2,3,6,8,9,21,31,100,101 -> 102) — dono das rotinas/OPs
--   Relacionamento 1:N: public.convenios.id_integrador = 102 (nunca hardcode)
--
-- Credenciais NÃO entram em migration (dados sensíveis): usar
--   backend/scripts/seed_caesan.py
-- Idempotente: ON CONFLICT / NOT EXISTS / guards.
-- ============================================================

-- ── 1. Convênio 16 (não renomeia se já existir: a importação Evoluir
--        resolve convênios por nome de plano, ex. "Caesan Convencional") ──
INSERT INTO public.convenios (id_convenio, nome)
VALUES (16, 'CAESAN')
ON CONFLICT (id_convenio) DO NOTHING;

-- Manter a sequence sincronizada caso o id 16 já existisse por insert manual
SELECT setval(
    pg_get_serial_sequence('public.convenios', 'id_convenio'),
    GREATEST((SELECT COALESCE(MAX(id_convenio), 1) FROM public.convenios), 16)
);

-- ── 2. worker.integradores (fonte de verdade de runtime — espaço W) ──
INSERT INTO worker.integradores (id_integrador, nome, sigla, ativo, tipo_operacao)
VALUES (102, 'CAESAN', 'CAESAN', TRUE, 'convenio')
ON CONFLICT (id_integrador) DO UPDATE
SET nome = EXCLUDED.nome,
    sigla = EXCLUDED.sigla,
    ativo = EXCLUDED.ativo,
    tipo_operacao = EXCLUDED.tipo_operacao,
    updated_at = NOW();

-- ── 3. public.integradores (espelho admin — id_integrador alinhado ao espaço W) ──
INSERT INTO public.integradores (id_integrador, id_convenio, nome, sigla, tipo_operacao, ativo)
VALUES (102, 16, 'CAESAN', 'CAESAN', 'convenio', TRUE)
ON CONFLICT (id_integrador) DO UPDATE
SET id_convenio = EXCLUDED.id_convenio,
    nome = EXCLUDED.nome,
    sigla = EXCLUDED.sigla,
    tipo_operacao = EXCLUDED.tipo_operacao,
    ativo = EXCLUDED.ativo,
    updated_at = NOW();

SELECT setval(
    pg_get_serial_sequence('public.integradores', 'id_integrador'),
    GREATEST((SELECT COALESCE(MAX(id_integrador), 1) FROM public.integradores), 102)
);

-- ── 4. Relacionamento convênio → integrador (tradução usada pelo hub,
--        backend_worker e dispatcher;jobs continuam carregando id_convenio) ──
UPDATE public.convenios
SET id_integrador = 102
WHERE id_convenio = 16
  AND (id_integrador IS NULL OR id_integrador <> 102);

-- ── 5. Catálogo de rotinas do integrador (rotinas pertencem ao integrador) ──
-- 5a. worker.integrador_operacoes (runtime; op0_login é interno, não catalogado,
--     seguindo o padrão IPASGO)
INSERT INTO worker.integrador_operacoes (id_integrador, rotina, descricao, tipo_processamento, ativo, modo_execucao)
VALUES
    (102, 'op1_autorizacao',    'Solicitação de autorização SP-SADT no portal prestador CAESAN', 'local', TRUE, 'automatico'),
    (102, 'op2_consultar_guia', 'Consulta de status de solicitação/guia via webservice BIOS (ws2.bios.inf.br)', 'local', TRUE, 'automatico')
ON CONFLICT DO NOTHING;

-- 5b. public.integrador_operacoes (espelho admin)
INSERT INTO public.integrador_operacoes (id_integrador, id_convenio, rotina, descricao, tipo_processamento, ativo)
VALUES
    (102, 16, 'op1_autorizacao',    'Solicitação de autorização SP-SADT no portal prestador CAESAN', 'local', TRUE),
    (102, 16, 'op2_consultar_guia', 'Consulta de status de solicitação/guia via webservice BIOS (ws2.bios.inf.br)', 'local', TRUE)
ON CONFLICT (id_integrador, rotina) DO NOTHING;

-- 5c. public.convenio_operacoes (alimenta o dropdown do frontend)
INSERT INTO public.convenio_operacoes (id_convenio, descricao, valor)
SELECT v.id_convenio, v.descricao, v.rotina
FROM (VALUES
    (16, 'Autorização SP-SADT (OP1)', 'op1_autorizacao'),
    (16, 'Consultar Solicitação/Guia (OP2)', 'op2_consultar_guia')
) AS v(id_convenio, descricao, rotina)
WHERE NOT EXISTS (
    SELECT 1 FROM public.convenio_operacoes co
    WHERE co.id_convenio = v.id_convenio AND co.valor = v.rotina
);

-- ── 6. Habilitar o convênio 16 para os tenants Evoluir (14) e
--        Brincando e Aprendendo (15) — SEM credenciais (ver seed_caesan.py) ──
INSERT INTO public.user_convenios (user_id, id_convenio)
SELECT u.id, 16
FROM public.users u
WHERE u.id IN (14, 15)
  AND NOT EXISTS (
      SELECT 1 FROM public.user_convenios uc
      WHERE uc.user_id = u.id AND uc.id_convenio = 16
  );
