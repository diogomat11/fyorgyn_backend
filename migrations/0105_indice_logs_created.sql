-- ============================================================
-- Migration 0105: Índice de retenção de logs
-- Plano: retenção de jobs/logs (2026-08) — o serviço de retenção purga
-- worker.logs por created_at (153k+ linhas acumuladas desde 2026-06).
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_wlogs_created
    ON worker.logs (created_at);
