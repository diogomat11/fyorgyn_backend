-- Migration 0064: Standardize convenio_operacoes routine values to match filename conventions

-- Bradesco (1)
UPDATE convenio_operacoes SET valor = 'op0_login' WHERE id_convenio = 1 AND valor = '0';
UPDATE convenio_operacoes SET valor = 'op0_login_fature' WHERE id_convenio = 1 AND valor = '0_fature';
UPDATE convenio_operacoes SET valor = 'op1_solicitar_autorizacao' WHERE id_convenio = 1 AND valor = '1';
UPDATE convenio_operacoes SET valor = 'op1_consultar_guias_fature' WHERE id_convenio = 1 AND valor = '1_fature';

-- Unimed Anapolis (2)
UPDATE convenio_operacoes SET valor = 'op0_login' WHERE id_convenio = 2 AND valor = '0';
UPDATE convenio_operacoes SET valor = 'op1_consulta' WHERE id_convenio = 2 AND valor IN ('', '1');
UPDATE convenio_operacoes SET valor = 'op2_captura' WHERE id_convenio = 2 AND valor = '2';
UPDATE convenio_operacoes SET valor = 'op3_execucao' WHERE id_convenio = 2 AND valor = '3';

-- Unimed Goiania (3)
UPDATE convenio_operacoes SET valor = 'op0_login' WHERE id_convenio = 3 AND valor = '0';
UPDATE convenio_operacoes SET valor = 'op1_consulta' WHERE id_convenio = 3 AND valor IN ('', '1');
UPDATE convenio_operacoes SET valor = 'op2_captura' WHERE id_convenio = 3 AND valor = '2';
UPDATE convenio_operacoes SET valor = 'op3_execucao' WHERE id_convenio = 3 AND valor = '3';

-- IPASGO (6)
UPDATE convenio_operacoes SET valor = 'op0_login' WHERE id_convenio = 6 AND valor = '0';
UPDATE convenio_operacoes SET valor = 'op1_autorizar_facplan' WHERE id_convenio = 6 AND valor = '1';
UPDATE convenio_operacoes SET valor = 'op2_open_facplan' WHERE id_convenio = 6 AND valor = '2';
UPDATE convenio_operacoes SET valor = 'op3_import_guias' WHERE id_convenio = 6 AND valor = '3';
UPDATE convenio_operacoes SET valor = 'op4_confirma_guia' WHERE id_convenio = 6 AND valor = '4';
UPDATE convenio_operacoes SET valor = 'op5_impress_guia' WHERE id_convenio = 6 AND valor = '5';
UPDATE convenio_operacoes SET valor = 'op6_check_baixados' WHERE id_convenio = 6 AND valor = '6';
UPDATE convenio_operacoes SET valor = 'op7_fat_facplan' WHERE id_convenio = 6 AND valor = '7';
UPDATE convenio_operacoes SET valor = 'op8_check_facplan' WHERE id_convenio = 6 AND valor = '8';
UPDATE convenio_operacoes SET valor = 'op9_anexos_facplan' WHERE id_convenio = 6 AND valor = '9';
UPDATE convenio_operacoes SET valor = 'op10_recurso_glosa' WHERE id_convenio = 6 AND valor = '10';
UPDATE convenio_operacoes SET valor = 'op11_import_guias_api' WHERE id_convenio = 6 AND valor = '11';
UPDATE convenio_operacoes SET valor = 'op12_impressao_api' WHERE id_convenio = 6 AND valor = '12';
UPDATE convenio_operacoes SET valor = 'op13_criar_lote' WHERE id_convenio = 6 AND valor = '13';
UPDATE convenio_operacoes SET valor = 'op14_cancelar_lote' WHERE id_convenio = 6 AND valor = '14';

-- Sulamerica (8)
UPDATE convenio_operacoes SET valor = 'op0_login' WHERE id_convenio = 8 AND valor = '0';
UPDATE convenio_operacoes SET valor = 'op1_consulta' WHERE id_convenio = 8 AND valor = '1';
UPDATE convenio_operacoes SET valor = 'op2_captura' WHERE id_convenio = 8 AND valor = '2';
UPDATE convenio_operacoes SET valor = 'op3_execucao' WHERE id_convenio = 8 AND valor = '3';

-- Amil (9)
UPDATE convenio_operacoes SET valor = 'op0_login' WHERE id_convenio = 9 AND valor = '0';
UPDATE convenio_operacoes SET valor = 'op1_consulta' WHERE id_convenio = 9 AND valor = '1';
UPDATE convenio_operacoes SET valor = 'op2_captura' WHERE id_convenio = 9 AND valor = '2';
UPDATE convenio_operacoes SET valor = 'op3_execucao' WHERE id_convenio = 9 AND valor = '3';
