\set ON_ERROR_STOP on
\getenv participant_password LAB5_TPC_PASSWORD
BEGIN;
SELECT pg_advisory_xact_lock(54208);
SELECT 'CREATE ROLE pad_craft_participant LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='pad_craft_participant') \gexec
SELECT format('ALTER ROLE pad_craft_participant PASSWORD %L', :'participant_password') \gexec
CREATE SCHEMA IF NOT EXISTS lab5_craft;
REVOKE ALL ON SCHEMA lab5_craft FROM PUBLIC;
GRANT USAGE ON SCHEMA lab5_craft TO pad_craft_participant;
CREATE TABLE IF NOT EXISTS lab5_craft.participation (
    job_id uuid PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON lab5_craft.participation TO pad_craft_participant;
