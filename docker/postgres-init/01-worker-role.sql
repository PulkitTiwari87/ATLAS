-- Creates the least-privilege PostgreSQL role for Atlas worker processes.
--
-- Only role creation + CONNECT happen here, because this file runs via
-- Postgres's /docker-entrypoint-initdb.d/ mechanism, which executes
-- ONCE against a brand-new, empty database -- before Alembic has created
-- any tables. A GRANT on a specific table here would fail (the table
-- doesn't exist yet) and abort container startup entirely.
--
-- The actual per-table privileges are granted separately, after
-- `alembic upgrade head` has created the schema -- see
-- docker/grant-worker-privileges.sql and docs/operations/docker.md.
--
-- Also runs ONLY on first container creation against a fresh volume; an
-- existing `pgdata` volume from before this file was added will not run
-- it retroactively (standard Postgres image behavior).
CREATE ROLE atlas_worker WITH LOGIN PASSWORD 'atlas_worker_pass';
GRANT CONNECT ON DATABASE atlas_db TO atlas_worker;
