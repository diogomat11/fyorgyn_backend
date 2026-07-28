-- Migration 0073: Setup do convenio ABA_clmf (101), UNIMED INTERCAMBIO (21), IPASGO GERAL (31)

-- 1. Inserir convenios especificos se nao existirem
INSERT INTO convenios (id_convenio, nome) VALUES (101, 'ABA_clmf')
ON CONFLICT (id_convenio) DO UPDATE SET nome = EXCLUDED.nome;

INSERT INTO convenios (id_convenio, nome) VALUES (21, 'UNIMED INTERCAMBIO')
ON CONFLICT (id_convenio) DO UPDATE SET nome = EXCLUDED.nome;

INSERT INTO convenios (id_convenio, nome) VALUES (31, 'IPASGO GERAL')
ON CONFLICT (id_convenio) DO UPDATE SET nome = EXCLUDED.nome;

-- Atualizar a sequencia do postgres para nao colidir com IDs atribuidos manualmente
SELECT setval('convenios_id_convenio_seq', COALESCE((SELECT MAX(id_convenio) FROM convenios), 1));

-- 2. Operacoes para ABA_clmf (101)
INSERT INTO convenio_operacoes (id_convenio, descricao, valor) VALUES
  (101, 'Login Portal ABA', 'op0_login'),
  (101, 'Importar Agendamentos', 'op1_importar_agendamentos'),
  (101, 'Consultar Carteirinha Paciente', 'op2_consultar_carteirinha')
ON CONFLICT DO NOTHING;

-- 3. Copiar operacoes para UNIMED INTERCAMBIO (21) a partir do id_convenio=3 (Unimed Goiania) se ainda nao existirem
INSERT INTO convenio_operacoes (id_convenio, descricao, valor)
SELECT 21, descricao, valor 
FROM convenio_operacoes 
WHERE id_convenio = 3
  AND (descricao, valor) NOT IN (SELECT descricao, valor FROM convenio_operacoes WHERE id_convenio = 21);

-- 4. Copiar operacoes para IPASGO GERAL (31) a partir do id_convenio=6 (IPASGO) se ainda nao existirem
INSERT INTO convenio_operacoes (id_convenio, descricao, valor)
SELECT 31, descricao, valor 
FROM convenio_operacoes 
WHERE id_convenio = 6
  AND (descricao, valor) NOT IN (SELECT descricao, valor FROM convenio_operacoes WHERE id_convenio = 31);
