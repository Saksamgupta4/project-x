-- Run this in Supabase SQL Editor once

CREATE TABLE IF NOT EXISTS accounts (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password      TEXT NOT NULL,
    proxy_country TEXT DEFAULT 'in',
    name          TEXT,
    modules       JSONB DEFAULT '[]',
    instance_id   TEXT DEFAULT 'default',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_cookies (
    email      TEXT PRIMARY KEY,
    cookies    JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_progress (
    id           SERIAL PRIMARY KEY,
    email        TEXT NOT NULL,
    course_id    INTEGER NOT NULL,
    course_name  TEXT,
    status       TEXT DEFAULT 'pending',
    next_run_at  TIMESTAMPTZ DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    attempts     INTEGER DEFAULT 0,
    UNIQUE(email, course_id)
);

CREATE TABLE IF NOT EXISTS sim_logs (
    id         SERIAL PRIMARY KEY,
    email      TEXT,
    message    TEXT,
    level      TEXT DEFAULT 'info',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_progress_status_next
    ON account_progress(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_accounts_instance
    ON accounts(instance_id);
CREATE INDEX IF NOT EXISTS idx_progress_email
    ON account_progress(email);

-- Auto cleanup logs (keep last 2000)
CREATE OR REPLACE FUNCTION cleanup_logs()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM sim_logs WHERE id IN (
        SELECT id FROM sim_logs ORDER BY created_at DESC OFFSET 2000
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_cleanup_logs ON sim_logs;
CREATE TRIGGER trigger_cleanup_logs
    AFTER INSERT ON sim_logs
    FOR EACH ROW EXECUTE FUNCTION cleanup_logs();
