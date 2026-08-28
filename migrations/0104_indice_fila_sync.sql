-- ============================================================
-- Migration 0104: Índice da fila de consumo do loop de sync (egress)
-- Plano: correção de consumo de egress Supabase (2026-08-26)
--
-- O loop de sync do hub roda a query:
--   WHERE status='success' AND result_consumed=false ORDER BY updated_at LIMIT N
-- O índice existente idx_wjobs_result (user_id, status, result_consumed) começa
-- por user_id e não atende essa query (seq scan em worker.jobs a cada ciclo).
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_wjobs_sync_queue
    ON worker.jobs (updated_at)
    WHERE status = 'success' AND result_consumed = false;
