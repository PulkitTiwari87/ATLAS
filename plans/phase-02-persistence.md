# Phase 02 — Persistence

## Objective

Give Atlas a durable, transactional PostgreSQL persistence layer for the
Phase 01 domain entities (`Job`, `Task`, `Worker`), without coupling the
domain layer to SQLAlchemy or any other infrastructure concern.

## Scope

- SQLAlchemy ORM models mapping to the tables in `docs/database/schema.md`.
- Engine/session management for PostgreSQL (using `DATABASE_URL` from
  `atlas.config.get_settings()`, established in Phase 00).
- Alembic migrations that create the schema.
- Repositories (`JobRepository`, `TaskRepository`, `WorkerRepository`) that
  translate between domain objects (`atlas.domain.*`) and ORM rows.
- Transactional session handling for state-changing operations.
- Required indexes/constraints from `docs/database/schema.md`.
- Unit tests (mapping round-trip) and integration tests (real PostgreSQL via
  the Phase 00 Docker Compose service).

## Database Schema

Implement exactly the tables already documented in `docs/database/schema.md`:

- `jobs` — `id` (UUID PK), `name`, `status`, `priority`, `created_at`,
  `updated_at`.
- `tasks` — `id` (UUID PK), `job_id` (FK → jobs.id), `type`, `payload`
  (JSONB), `status`, `attempt`, `max_retries`, `worker_id` (FK →
  workers.id, nullable), `created_at`, `updated_at`.
- `workers` — `id` (UUID PK), `hostname`, `address`, `status`,
  `last_heartbeat`, `created_at`.
- `task_attempts` — `id` (UUID PK), `task_id` (FK → tasks.id), `worker_id`
  (FK → workers.id), `attempt_number`, `started_at`, `finished_at`,
  `result` (JSONB), `error`.

All foreign keys use `ON DELETE CASCADE`, matching `schema.md`.

`status` columns are stored as `VARCHAR` using the string values of the
Phase 01 enums (`JobStatus`, `TaskStatus`, `WorkerStatus`) — no separate
Postgres `ENUM` type, to keep future status additions a pure application-level
change (no migration required to add a status value).

## SQLAlchemy / Model Strategy

- SQLAlchemy 2.0 declarative models (`DeclarativeBase`), one class per table,
  in `src/atlas/persistence/models.py`.
- ORM models are infrastructure-only: no business logic, no state-transition
  rules. They are a private mapping detail behind the repositories.
- Domain entities (`atlas.domain.Job`/`Task`/`Worker`) remain the objects the
  rest of the codebase works with; ORM model classes are never imported
  outside `atlas.persistence`.

## Connection / Session Management

- `src/atlas/persistence/db.py`: builds the SQLAlchemy `Engine` from
  `settings.database_url`, and exposes a `session_scope()` context manager
  (commit on success, rollback on exception, always close).
- No global mutable session; each unit of work gets its own session from a
  `sessionmaker`.
- Pooling uses SQLAlchemy's default `QueuePool` — no manual pool tuning
  without evidence it's needed (Ponytail: avoid premature optimization).

## Repositories

One repository per aggregate, in `src/atlas/persistence/repositories.py`:

- `JobRepository`: `add(job)`, `get(id)`, `update(job)`, `list_by_status(status)`.
- `TaskRepository`: `add(task)`, `get(id)`, `update(task)`, `list_by_job(job_id)`.
- `WorkerRepository`: `add(worker)`, `get(id)`, `update(worker)`, `list_by_status(status)`.

Each method accepts/returns Phase 01 domain objects and performs
domain-object ↔ ORM-row mapping internally. Repositories take a SQLAlchemy
`Session` explicitly (passed in, not owned) so transaction boundaries stay
with the caller via `session_scope()`.

`task_attempts` gets a table + ORM model (schema completeness, and FK targets
must exist), but **no repository yet** — nothing writes attempt records until
Phase 09 (Recovery + Retries) needs them. Adding a repository with no caller
would be premature (YAGNI).

## Transactions

- Every state transition that must be durable (`job.transition_to(...)` then
  persisted, etc.) happens inside a single `session_scope()` block: load →
  mutate domain object → repository `update()` → commit.
- No cross-repository distributed transactions needed yet — a single
  PostgreSQL session/transaction covers all Phase 02 use cases.
- Optimistic concurrency (version columns) is **not** implemented — not
  required until concurrent scheduler/dispatcher writes exist (Phase 05/06).

## Migrations

- Alembic initialized in `alembic/`, configured against `settings.database_url`.
- One initial migration creating `jobs`, `tasks`, `workers`, `task_attempts`
  with the indexes/constraints below.
- No data migrations needed (no existing production data).

## Domain ↔ Persistence Mapping

- Mapping functions live next to the repositories (`_to_domain(row)` /
  `_to_row(entity)` per entity), not on the domain classes themselves — the
  domain layer stays free of persistence imports (Phase 01 constraint
  preserved).
- Enum columns map via `.value` / `Enum(str_value)` — straightforward, no
  custom SQLAlchemy `TypeDecorator` unless plain mapping proves insufficient.

## Required Indexes / Constraints

From `docs/database/schema.md`:

- `jobs.status`
- `tasks.status`
- `tasks.worker_id`
- `workers.status`
- `task_attempts.task_id`

Plus the primary keys and foreign keys listed under Database Schema above.

## Tests

- **Unit** (`tests/persistence/test_mapping.py`): domain → ORM row → domain
  round-trip preserves all fields, for each entity. No real DB needed (uses
  in-memory construction of ORM objects, not a session).
- **Integration** (`tests/persistence/test_repositories.py`, requires the
  Phase 00 `postgres` Docker Compose service running): add/get/update for
  each repository; FK/cascade-delete behavior; unique/index constraints;
  transaction rollback on error leaves no partial row.
- Integration tests are skipped automatically (`pytest.mark.skipif` / a
  connection check) when PostgreSQL is not reachable, so `pytest` still
  passes in environments without Docker (as established in Phase 00).

## Acceptance Criteria

- [ ] Alembic migration creates all four tables with the documented columns,
      indexes, and FK/cascade constraints.
- [ ] `JobRepository`, `TaskRepository`, `WorkerRepository` support add/get/
      update, mapping correctly to/from `atlas.domain` objects.
- [ ] All repository writes happen inside a transaction that commits on
      success and rolls back cleanly on error.
- [ ] Domain layer (`src/atlas/domain/`) has zero new imports from
      `atlas.persistence` or SQLAlchemy (Phase 01 stays infrastructure-free).
- [ ] Unit mapping tests pass without a database.
- [ ] Integration tests pass against the Phase 00 `postgres` Docker service.
- [ ] `pytest` still passes with no PostgreSQL running (integration tests
      skip cleanly).

## Out of Scope

Do NOT implement in this phase:

- Job/Task REST or gRPC API endpoints (Phase 03/10).
- Scheduler, priority queue, dispatch (Phase 05/06).
- Worker runtime / heartbeats / failure detection (Phase 04/07/08).
- Retry/recovery logic or a `task_attempts` repository (Phase 09).
- Optimistic concurrency / version columns.
- Redis, Kafka, RabbitMQ, Celery, Kubernetes, or any other new
  infrastructure.
- Connection-pool tuning or query performance optimization without
  evidence of need.
