-- Migration 0024: Add params field to jobs table
ALTER TABLE jobs ADD COLUMN params JSONB;
