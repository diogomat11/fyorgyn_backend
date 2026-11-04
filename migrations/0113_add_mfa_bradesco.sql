-- ============================================================
-- 0113 — MFA Bradesco (Faturi): colunas de trânsito do código MFA em worker.jobs
-- Plano: docs/plano_mfa_bradesco.md (D2/D3/D9) — job continua processing durante a espera;
-- estado MFA é derivado das colunas (sem novo status de job).
-- Segurança: mfa_code é apagado após consumo e NUNCA deve ser logado nem retornado por API.
-- Idempotente: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_required_at  TIMESTAMPTZ;
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_expires_at   TIMESTAMPTZ;
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_code         TEXT;
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_submitted_at TIMESTAMPTZ;
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_consumed_at  TIMESTAMPTZ;
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_cancelled    BOOLEAN DEFAULT FALSE;
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS mfa_attempts     INT DEFAULT 0;

COMMENT ON COLUMN worker.jobs.mfa_required_at  IS 'Momento em que o worker detectou a tela de código MFA (Faturi Bradesco)';
COMMENT ON COLUMN worker.jobs.mfa_expires_at   IS 'Prazo do ciclo atual (mfa_required_at + 300s) — guia o countdown do modal';
COMMENT ON COLUMN worker.jobs.mfa_code         IS 'Código MFA em trânsito — apagar após consumo; NUNCA logar nem expor por API';
COMMENT ON COLUMN worker.jobs.mfa_submitted_at IS 'Momento em que o hub aceitou o código digitado pelo usuário';
COMMENT ON COLUMN worker.jobs.mfa_consumed_at  IS 'Momento em que o worker consumiu o código (UPDATE...RETURNING atômico) — código já apagado';
COMMENT ON COLUMN worker.jobs.mfa_cancelled    IS 'Usuário cancelou a digitação — worker aborta o login em até 3s';
COMMENT ON COLUMN worker.jobs.mfa_attempts     IS 'Ciclos de novo pedido (código inválido faz o portal re-pedir) — máx. 2';
