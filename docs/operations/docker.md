# Docker Development

## Services

- **postgres** – PostgreSQL instance for persistent storage.
- **control-plane** – Atlas server exposing REST/gRPC APIs.
- **worker-1**, **worker-2**, ... – Placeholder worker containers.

## Building & Running

```bash
# Build the Atlas image (Dockerfile to be added later)
docker compose build

# Start the stack
docker compose up -d
```

The control plane will be reachable at `http://localhost:8000` and gRPC at `localhost:50051`.

## Stopping

```bash
docker compose down
```

All data is stored in a Docker volume `pgdata`.

## Worker database credentials

Worker processes (`worker-1`, ...) connect to PostgreSQL as a separate,
least-privilege `atlas_worker` role instead of the control plane's `atlas`
role — `WorkerRuntime` only ever reads/writes `workers` and `tasks`, and
inserts into `task_attempts`; it never touches `jobs`. See
`docker/postgres-init/01-worker-role.sql` for the exact grants.

The role itself is created automatically on first `docker compose up`
(Postgres's `/docker-entrypoint-initdb.d/` mechanism), but its per-table
privileges cannot be granted at that point — the tables don't exist yet
until Alembic runs. After running migrations, grant them once:

```bash
make grant-worker-privileges
```

**What this does and does not provide**: this separates worker and
control-plane database credentials and limits which tables/operations a
compromised or buggy worker process can reach at the SQL level. It does
**not** provide network isolation, container sandboxing, or secrets
management — both roles' passwords are still plaintext defaults in
`docker-compose.yml`/`.env.example`, suitable for local development only.
A production deployment should generate real passwords and manage them
via whatever secrets mechanism its platform provides (outside Atlas's
scope, per `CLAUDE.md` §16 — no such infrastructure is bundled here).
