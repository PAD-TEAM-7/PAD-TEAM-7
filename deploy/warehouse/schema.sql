-- Serialize schema initialization and publication for overlapping ETL jobs.
SELECT pg_advisory_xact_lock(54105);
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE TABLE IF NOT EXISTS analytics.snapshots (
    captured_at timestamptz NOT NULL,
    service text NOT NULL,
    metric text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    PRIMARY KEY (captured_at, service, metric)
);
CREATE OR REPLACE VIEW analytics.latest AS
    SELECT captured_at, service, metric, row_count
    FROM analytics.snapshots
    WHERE captured_at = (SELECT max(captured_at) FROM analytics.snapshots);
