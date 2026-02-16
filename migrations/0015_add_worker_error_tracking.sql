-- Migration: 0015_add_worker_error_tracking.sql
-- Description: Add first_error_at column to workers table for auto-restart logic

ALTER TABLE workers ADD COLUMN IF NOT EXISTS first_error_at TIMESTAMP WITH TIME ZONE;
