-- Migration 0089: Add status column to unidades table
-- Author: Antigravity AI
-- Date: 2026-08-12

ALTER TABLE public.unidades ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'Ativo';
