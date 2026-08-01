-- Run this once in Supabase SQL Editor before deploying the updated backend.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.ads_hidden_rows_state (
    id INTEGER PRIMARY KEY,
    hidden_rows JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.production_smart_segment_sheets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE IF EXISTS public.production_smart_segment_rows
    ADD COLUMN IF NOT EXISTS row_color VARCHAR(40) NOT NULL DEFAULT '';

ALTER TABLE IF EXISTS public.production_smart_segment_sheets
    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS public.scheduling_sheets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE IF EXISTS public.scheduling_rows
    ADD COLUMN IF NOT EXISTS sheet_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_scheduling_rows_sheet_id
    ON public.scheduling_rows (sheet_id);

CREATE TABLE IF NOT EXISTS public.app_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
