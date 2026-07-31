-- Migration 0081: Backfill das OPs op3/op4/op5 do ABA_CLMF (101) no schema public
-- Description: Alinha o catalogo public.convenio_operacoes com o worker.convenio_operacoes
--              para o convenio ABA_CLMF (101).
--
-- Contexto (lacuna pre-existente):
--   A migration 0074_create_motivos_faltas_and_workflow_ops.sql inseriu op3/op4/op5
--   APENAS no schema worker (linhas 19-24), mas nao no schema public. As OPs op0/op1/op2
--   ja existiam em public (via 0073_aba_clmf_setup.sql) e a op6 foi adicionada em ambos
--   os schemas pela migration 0079_register_op6_atualizar_rc.sql.
--
--   Resultado antes desta migration:
--     - public.convenio_operacoes (101): op0_login, op1_importar_agendamentos,
--       op2_consultar_carteirinha, op6_atualizar_rc  (FALTAM op3/op4/op5)
--     - worker.convenio_operacoes (101): op1, op2, op3, op4, op5, op6  (completo)
--
--   Esta migration completa o catalogo public para refletir todas as 7 OPs do ABA_CLMF.
--   Impacto funcional: nenhum (o roteamento em runtime e por worker.jobs.rotina + braços
--   em guias_sync_service.py); esta correcao e apenas de consistencia do catalogo.

INSERT INTO public.convenio_operacoes (id_convenio, descricao, valor) VALUES
  (101, 'Login Portal ABA', 'op0_login'),
  (101, 'Importar Agendamentos', 'op1_importar_agendamentos'),
  (101, 'Consultar Carteirinha Paciente', 'op2_consultar_carteirinha'),
  (101, 'Confirmar/remover confirmação no portal ABA CLMF', 'op3_confirmar_agendamento'),
  (101, 'Registrar falta em bloco no portal ABA CLMF', 'op4_registrar_falta'),
  (101, 'Remover falta no portal ABA CLMF', 'op5_remover_falta'),
  (101, 'Atualizar Relatorio Clinico Mensal (RC) e baixar PDF', 'op6_atualizar_rc')
ON CONFLICT DO NOTHING;
