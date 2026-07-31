-- Migration 0082: Deduplicar OPs do ABA_CLMF (101) em public e adicionar constraint UNIQUE
-- Description: Corrige as duplicatas introduzidas pela migration 0081 (que usou
--              ON CONFLICT DO NOTHING, mas a tabela public.convenio_operacoes nao tinha
--              constraint UNIQUE em (id_convenio, valor), resultando em duplicacao das
--              OPs op0/op1/op2/op6 do convenio 101).
--
--              Adicionalmente, alinha o schema public com o worker adicionando a
--              constraint UNIQUE (id_convenio, valor) - que o worker.convenio_operacoes
--              ja possui (convenio_operacoes_id_convenio_rotina_key) - prevenindo
--              duplicacao futura.
--
-- Acao:
--   1. Remove duplicatas mantendo o menor id de cada par (id_convenio, valor).
--   2. Adiciona constraint UNIQUE (id_convenio, valor).

-- 1. Deduplicacao: remover linhas com id maior para cada (id_convenio, valor)
DELETE FROM public.convenio_operacoes
WHERE id IN (
    SELECT id FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY id_convenio, valor
                ORDER BY id ASC
            ) AS rn
        FROM public.convenio_operacoes
    ) ranked
    WHERE ranked.rn > 1
);

-- 2. Adicionar constraint UNIQUE (id_convenio, valor) - alinha com worker schema
--    (worker.convenio_operacoes_id_convenio_rotina_key usa rotina; public usa valor)
CREATE UNIQUE INDEX IF NOT EXISTS convenio_operacoes_id_convenio_valor_key
    ON public.convenio_operacoes (id_convenio, valor);
