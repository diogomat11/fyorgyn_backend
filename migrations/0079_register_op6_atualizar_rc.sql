-- Migration 0079: Registrar OP6 "op6_atualizar_rc" no convenio ABA_CLMF (id_convenio = 101)
-- Description: Adiciona a OP6 (Atualizar Relatorio Clinico Mensal + baixar PDF) ao convenio
--              ABA_CLMF (101), como sequencia seguinte a op5_remover_falta.
--              Nao cria novo convenio - apenas adiciona a nova operacao as tabelas existentes.
--
-- Referencias:
--   - Plano: Prompts_implantacoes/Contextos/2026_07_28_Plano_OP5_CLMF_Valida_Prestador.md
--   - Origem (replica): clmf_hub_basic/worker/Worker/clmf_scraper.py (rotina clmf_atualizar_rc)
--   - Portal: https://abalarissamartinsferreira.com.br (mesmo portal do ABA_CLMF)

-- 1. Registrar OP6 no schema public (tabela convenio_operacoes usada pelo Hub)
INSERT INTO public.convenio_operacoes (id_convenio, descricao, valor)
VALUES (101, 'Atualizar Relatorio Clinico Mensal (RC) e baixar PDF', 'op6_atualizar_rc')
ON CONFLICT DO NOTHING;

-- 2. Registrar OP6 no schema worker (tabela worker.convenio_operacoes usada pelo dispatcher/worker)
INSERT INTO worker.convenio_operacoes (id_convenio, rotina, descricao, ativo)
VALUES (101, 'op6_atualizar_rc', 'Atualizar Relatorio Clinico Mensal (RC) e baixar PDF no portal ABA CLMF', true)
ON CONFLICT DO NOTHING;
