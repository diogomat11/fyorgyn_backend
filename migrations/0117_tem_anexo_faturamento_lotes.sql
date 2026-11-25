-- 0117 — Verificação de anexos por guia na Conferência de Faturamento IPASGO (OP17 + OP19)
--
-- Adiciona a coluna `tem_anexo` (BOOLEAN) em `faturamento_lotes` para registrar
-- se a guia possui anexo ativo (Excluido=false) no portal IPASGO (via
-- /ConferenciaFaturamento/ListarAnexosGuia na OP17 ou se o objeto Guia em
-- ObterProcedimentos já informa TemAnexo=true, e atualizado imediatamente
-- após o sucesso da OP19).

ALTER TABLE faturamento_lotes
    ADD COLUMN IF NOT EXISTS tem_anexo BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN faturamento_lotes.tem_anexo IS
    'Indica se a guia deste item possui anexo ativo no portal ConferenciaFaturamento do IPASGO (ListarAnexosGuia / TemAnexo na OP17 e após upload na OP19).';
