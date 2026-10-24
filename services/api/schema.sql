-- Applied by the API on startup; safe to run more than once
CREATE TABLE IF NOT EXISTS monitors (
    id               SERIAL PRIMARY KEY,
    name             TEXT        NOT NULL,
    url              TEXT        NOT NULL,
    interval_seconds INTEGER     NOT NULL DEFAULT 60 CHECK (interval_seconds >= 10),
    timeout_seconds  INTEGER     NOT NULL DEFAULT 10 CHECK (timeout_seconds BETWEEN 1 AND 30),
    expected_status  INTEGER     NOT NULL DEFAULT 200,
    enabled          BOOLEAN     NOT NULL DEFAULT TRUE,
    next_run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS checks (
    id          BIGSERIAL PRIMARY KEY,
    monitor_id  INTEGER     NOT NULL REFERENCES monitors (id) ON DELETE CASCADE,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ok          BOOLEAN     NOT NULL,
    status_code INTEGER,
    latency_ms  INTEGER,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS checks_monitor_time ON checks (monitor_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS monitors_due ON monitors (next_run_at) WHERE enabled;

-- Browser checks. ALTER ... IF NOT EXISTS upgrades databases created before them.
ALTER TABLE monitors ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'http' CHECK (kind IN ('http', 'browser'));
ALTER TABLE monitors ADD COLUMN IF NOT EXISTS steps JSONB NOT NULL DEFAULT '[]';
-- A JPEG of the page, kept only when a browser check fails
ALTER TABLE checks ADD COLUMN IF NOT EXISTS screenshot BYTEA;
