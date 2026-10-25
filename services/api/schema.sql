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

-- Incidents: one opens when a monitor fails failures_to_alert checks in a row, and the next good
-- check resolves it. A trigger decides, so both runners get it and it commits with the check.
ALTER TABLE monitors ADD COLUMN IF NOT EXISTS failures_to_alert INTEGER NOT NULL DEFAULT 2
    CHECK (failures_to_alert BETWEEN 1 AND 10);

CREATE TABLE IF NOT EXISTS incidents (
    id                   SERIAL PRIMARY KEY,
    monitor_id           INTEGER     NOT NULL REFERENCES monitors (id) ON DELETE CASCADE,
    opened_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at          TIMESTAMPTZ,
    cause                TEXT,
    -- Set by the notifier once the message is sent; NULL means it still has to be sent
    opened_notified_at   TIMESTAMPTZ,
    resolved_notified_at TIMESTAMPTZ
);

-- At most one open incident per monitor
CREATE UNIQUE INDEX IF NOT EXISTS incidents_one_open ON incidents (monitor_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS incidents_to_notify ON incidents (id)
    WHERE opened_notified_at IS NULL OR (resolved_at IS NOT NULL AND resolved_notified_at IS NULL);

CREATE OR REPLACE FUNCTION track_incident() RETURNS trigger AS $$
DECLARE
    needed INTEGER;
BEGIN
    IF NEW.ok THEN
        UPDATE incidents SET resolved_at = NEW.checked_at
        WHERE monitor_id = NEW.monitor_id AND resolved_at IS NULL;
        RETURN NULL;
    END IF;

    SELECT failures_to_alert INTO needed FROM monitors WHERE id = NEW.monitor_id;
    -- Open only when the last `needed` checks, this one included, all failed
    IF (SELECT count(*) FILTER (WHERE NOT ok) FROM (
            SELECT ok FROM checks WHERE monitor_id = NEW.monitor_id
            ORDER BY checked_at DESC, id DESC LIMIT needed) AS latest) = needed THEN
        INSERT INTO incidents (monitor_id, opened_at, cause) VALUES (NEW.monitor_id, NEW.checked_at, NEW.error)
        ON CONFLICT (monitor_id) WHERE resolved_at IS NULL DO NOTHING;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER checks_track_incident AFTER INSERT ON checks
    FOR EACH ROW EXECUTE FUNCTION track_incident();
