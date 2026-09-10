-- Grants the atlas_worker role (created by
-- docker/postgres-init/01-worker-role.sql) exactly the privileges
-- WorkerRuntime (src/atlas/worker/runtime.py) actually needs -- nothing
-- on `jobs`, no DELETE, no schema/DDL rights anywhere.
--
-- Run this ONCE, after `alembic upgrade head` has created the schema
-- (this file cannot run automatically at container-init time -- the
-- tables don't exist yet then; see 01-worker-role.sql for why):
--
--   docker compose exec -T postgres psql -U atlas -d atlas_db \
--       < docker/grant-worker-privileges.sql
--
-- or: make grant-worker-privileges
GRANT SELECT, INSERT, UPDATE ON workers TO atlas_worker;
GRANT SELECT, UPDATE ON tasks TO atlas_worker;
GRANT INSERT ON task_attempts TO atlas_worker;
