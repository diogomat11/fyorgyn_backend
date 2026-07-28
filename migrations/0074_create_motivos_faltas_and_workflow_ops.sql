-- Migration: 0074_create_motivos_faltas_and_workflow_ops.sql
-- Description: Cria tabela motivos_faltas no schema public e registra OPs de workflow no schema worker

-- 1. Tabela de Motivos de Falta no schema public
CREATE TABLE IF NOT EXISTS public.motivos_faltas (
    id SERIAL PRIMARY KEY,
    descricao TEXT NOT NULL,
    user_id INTEGER REFERENCES public.users(id) ON DELETE SET NULL,
    id_mapeado INTEGER,
    status TEXT DEFAULT 'Ativo',
    tipo TEXT,
    anexo TEXT DEFAULT 'NÃO'
);

-- Index para busca por status e tipo
CREATE INDEX IF NOT EXISTS idx_motivos_faltas_status_tipo ON public.motivos_faltas (status, tipo);

-- 2. Registrar OPs de Workflow no schema worker para ABA CLMF (id_convenio = 101)
INSERT INTO worker.convenio_operacoes (id_convenio, rotina, descricao, ativo)
VALUES
  (101, 'op3_confirmar_agendamento', 'Confirmar/remover confirmação no portal ABA CLMF', true),
  (101, 'op4_registrar_falta', 'Registrar falta em bloco no portal ABA CLMF', true),
  (101, 'op5_remover_falta', 'Remover falta no portal ABA CLMF', true)
ON CONFLICT DO NOTHING;

-- 3. Seed dos motivos padrão (de-para com id_mapeado do portal ABA CLMF)
INSERT INTO public.motivos_faltas (id, descricao, status, tipo, anexo, id_mapeado, user_id) VALUES
  (1, 'Atestado (anexar documento em sistema)', 'Ativo', 'Paciente', 'SIM', 1, 1),
  (2, 'Férias (uma semana por ano) - Paciente', 'Ativo', 'Paciente', 'NÃO', 2, 1),
  (3, 'Sintomas COVID (não enviou resultado teste)', 'Ativo', 'Paciente', 'NÃO', 3, 1),
  (4, 'Sem Justificativa (não nos comunicou por nenhum meio)', 'Ativo', 'Paciente', 'NÃO', 4, 1),
  (5, 'Condições externas', 'Ativo', 'Paciente', 'NÃO', 5, 1),
  (6, 'Condições físicas e familiares', 'Ativo', 'Paciente', 'NÃO', 6, 1),
  (7, 'Não aceitou substituição (foi ofertada)', 'Ativo', 'Profissional', 'NÃO', 7, 1),
  (8, 'Doença (sem atestado médico)', 'Ativo', 'Paciente', 'NÃO', 8, 1),
  (9, 'Paciente sem guia', 'Ativo', 'Paciente', 'NÃO', 10, 1),
  (10, 'Falecimento de parente próximo', 'Ativo', 'Paciente', 'NÃO', 11, 1),
  (11, 'Viagem', 'Ativo', 'Paciente', 'NÃO', 12, 1),
  (12, 'Atestado (aguardando envio)', 'Ativo', 'Paciente', 'NÃO', 13, 1),
  (13, 'Jogos da copa', 'Ativo', 'Paciente', 'NÃO', 14, 1),
  (14, 'Testou Positivo para Covid (anexar resultado)', 'Ativo', 'Paciente', 'SIM', 15, 1),
  (15, 'Atendimentos de 15 em 15 dias', 'Ativo', 'Paciente', 'NÃO', 16, 1),
  (16, 'Sem profissional para substituir', 'Inativo', 'Profissional', 'NÃO', 17, 1),
  (17, 'Em consequência de substituição', 'Ativo', 'Paciente', 'NÃO', 18, 1),
  (18, 'Falta do profissional', 'Ativo', 'Profissional', 'NÃO', 19, 1),
  (19, 'Psicoterapia, PEI, Neurofeedback - Especifico', 'Inativo', 'Paciente', 'NÃO', 20, 1),
  (20, 'Terapeuta - Curso', 'Ativo', 'Profissional', 'NÃO', 21, 1),
  (21, 'Terapeuta - Férias (uma por semestre)', 'Ativo', 'Profissional', 'NÃO', 22, 1),
  (22, 'PENDENCIA FINANCEIRA', 'Ativo', 'Paciente', 'NÃO', 23, 1),
  (23, 'BLOQUEIO - PEI - SEMANAL', 'Ativo', 'Paciente', 'NÃO', 24, 1),
  (24, 'BLOQUEIO - PEI - MENSAL', 'Ativo', 'Paciente', 'NÃO', 25, 1),
  (25, 'BLOQUEIO - PEI - VENCIDO', 'Ativo', 'Paciente', 'NÃO', 26, 1),
  (26, 'PEI - POR JUNTA MEDICA', 'Ativo', 'Paciente', 'NÃO', 27, 1),
  (27, 'Abandono de terapia', 'Ativo', 'Paciente', 'NÃO', 28, 1),
  (28, 'Escala de trabalho 12x36', 'Ativo', 'Paciente', 'NÃO', 29, 1),
  (29, 'Atraso - 15 min', 'Ativo', 'Paciente', 'NÃO', 30, 1),
  (30, 'Terapeuta - Atestado (aguardando envio)', 'Ativo', 'Profissional', 'NÃO', 31, 1),
  (31, 'Terapeuta - Atestado (anexar documento em sistema)', 'Ativo', 'Profissional', 'SIM', 32, 1),
  (32, 'Terapeuta - Condições externas', 'Ativo', 'Profissional', 'NÃO', 33, 1),
  (33, 'teste', 'Ativo', 'Paciente', 'NÃO', 34, 1),
  (34, 'Terapeuta - Atraso - 15 min', 'Ativo', 'Profissional', 'NÃO', 35, 1),
  (35, 'Terapeuta - Remanejamento', 'Ativo', 'Profissional', 'NÃO', 36, 1),
  (36, 'Terapeuta - Reunião escolar', 'Ativo', 'Profissional', 'NÃO', 37, 1),
  (37, 'Terapeuta - Reunião diretoria clínica', 'Ativo', 'Profissional', 'NÃO', 38, 1),
  (38, 'Terapeuta - Doença sem atestado médico', 'Ativo', 'Profissional', 'NÃO', 39, 1),
  (39, 'Terapeuta - Sem Justificativa (não nos comunicou por nenhum meio)', 'Ativo', 'Profissional', 'NÃO', 40, 1)
ON CONFLICT (id) DO UPDATE SET
  descricao = EXCLUDED.descricao,
  status = EXCLUDED.status,
  tipo = EXCLUDED.tipo,
  anexo = EXCLUDED.anexo,
  id_mapeado = EXCLUDED.id_mapeado;

SELECT setval('public.motivos_faltas_id_seq', (SELECT MAX(id) FROM public.motivos_faltas));
