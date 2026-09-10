# Docker Development

## Services

- **postgres** – PostgreSQL instance for persistent storage.
- **control-plane** – the one process that owns the shared
  `GrpcWorkerRegistry`: REST API (`:8000`), gRPC server (`:50051`),
  `DispatchLoop` (Scheduler + Dispatcher), `FailureDetector`, and
  `RecoveryManager`. On startup it runs `alembic upgrade head` and grants
  the `atlas_worker` role its table privileges (see below) — nothing
  manual is required for a fresh deployment.
- **worker-1** – a gRPC worker process. Additional workers can be added
  as `worker-2`, `worker-3`, ... following the same service definition
  with a different container name.

`GrpcWorkerRegistry` is in-process state, so the gRPC server and
everything that dispatches through it must run in the same process — see
`docs/decisions/ADR-001-control-plane.md`. There is deliberately no
separate `grpc-server` service.

## Building & Running

```bash
docker compose up --build
```

The control plane is reachable at `http://localhost:8000` (REST) and
`localhost:50051` (gRPC, mainly useful for other tooling — workers connect
to it automatically via `ATLAS_GRPC_TARGET=control-plane:50051`).

`worker-1` waits on the control plane's health check
(`GET /health`) before starting, so a fresh `docker compose up` needs no
manual ordering.

## Verifying it's alive

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"name": "demo", "priority": 10, "tasks": [{"type": "shell", "payload": {"command": "echo hi"}}]}'

curl http://localhost:8000/jobs/<id-from-above>
```

The job should reach `COMPLETED` within a couple of dispatch cycles
(default poll interval: 1 second).

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
(Postgres's `/docker-entrypoint-initdb.d/` mechanism), and the
control plane grants its per-table privileges automatically on every
startup (after running migrations, using its own `atlas` admin
credentials — the worker never needs elevated access to grant its own
privileges). This replaces the old manual
`make grant-worker-privileges` step for a fresh Docker deployment; that
target still exists for a non-Docker Postgres instance the control plane
doesn't manage.

**What this does and does not provide**: this separates worker and
control-plane database credentials and limits which tables/operations a
compromised or buggy worker process can reach at the SQL level. It does
**not** provide network isolation, container sandboxing, or secrets
management — both roles' passwords are still plaintext defaults in
`docker-compose.yml`/`.env.example`, suitable for local development only.
A production deployment should generate real passwords and manage them
via whatever secrets mechanism its platform provides (outside Atlas's
scope, per `CLAUDE.md` §16 — no such infrastructure is bundled here).
