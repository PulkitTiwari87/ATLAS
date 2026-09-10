# Phase 03 — Job API

## Objective

Expose job submission, retrieval, listing, and cancellation over REST, backed
by the Phase 01 domain model and Phase 02 persistence layer, with business
logic kept out of route handlers per `CLAUDE.md` §14 (API → service/domain
logic → persistence).

## Scope

- REST endpoints: `POST /jobs`, `GET /jobs`, `GET /jobs/{job_id}`,
  `POST /jobs/{job_id}/cancel` (from `docs/api/rest-api.md`).
- A `JobService` that owns job-lifecycle business logic and talks to
  `JobRepository`/`TaskRepository` from Phase 02.
- Pydantic request/response schemas.
- Wiring these routes into the existing `src/atlas/main.py` FastAPI app
  (Phase 00).
- API-level tests (unit tests on the service with a real Postgres session,
  and HTTP tests via FastAPI's `TestClient`).

## REST API Contract

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jobs` | Submit a new job with its initial tasks. |
| `GET` | `/jobs` | List jobs, optionally filtered by `status`. |
| `GET` | `/jobs/{job_id}` | Retrieve one job with its tasks. |
| `POST` | `/jobs/{job_id}/cancel` | Cancel a job that is not yet terminal. |

`GET /workers`, `GET /workers/{worker_id}`, and `GET /ready` from
`docs/api/rest-api.md` are **not** part of this phase (see Out of Scope).
`GET /health` already exists from Phase 00 and is unchanged.

## Job Creation Flow

1. Client `POST`s `{name, priority, tasks: [{type, payload}, ...]}`.
2. Route handler validates the request body via Pydantic and calls
   `JobService.create_job(...)`.
3. `JobService` builds one `Job` domain object (status defaults to
   `SUBMITTED`, per Phase 01) and one `Task` domain object per requested
   task (status defaults to `PENDING`), then persists all of them —
   `JobRepository.add` + `TaskRepository.add` per task — inside a single
   `session_scope()` transaction.
4. No scheduling happens here: the job stays `SUBMITTED` and tasks stay
   `PENDING`. Moving a job to `QUEUED` is the Scheduler's job (Phase 05) and
   is explicitly out of scope.
5. Response: `201 Created` with the created job (id, name, priority, status,
   timestamps) and its tasks.

## Job Retrieval

`GET /jobs/{job_id}`: `JobService.get_job(job_id)` loads the job via
`JobRepository.get` and its tasks via `TaskRepository.list_by_job`, both in
one read-only `session_scope()`. `404` if the job does not exist.

## Job Listing

`GET /jobs?status=<JobStatus>` (status filter optional):
`JobService.list_jobs(status=None)` calls `JobRepository.list_by_status`
when a filter is given, otherwise `JobRepository.list_all` (see Repository
Interaction — this method does not exist yet and must be added). Returns an
array of job summaries (no embedded tasks, to keep the list endpoint cheap —
`GET /jobs/{job_id}` is where task detail lives).

## Job Cancellation

`POST /jobs/{job_id}/cancel`:

1. Load the job. `404` if missing.
2. `job.transition_to(JobStatus.CANCELLED)` — reuses the Phase 01 state
   machine, so an already-terminal job (`COMPLETED`/`FAILED`/`CANCELLED`)
   raises `InvalidTransitionError`, mapped to `409 Conflict`.
3. On success, also cancel the job's own non-terminal tasks (`PENDING`,
   `ASSIGNED`, `RUNNING` → `CANCELLED` via each task's `transition_to`), so
   no task is left referencing a cancelled job as if it were still
   schedulable. This is still job-lifecycle bookkeeping (not scheduling or
   dispatch) and happens in the same transaction as the job update.
4. Response: `200 OK` with the updated job.

## Request / Response Schemas

```
TaskCreate:
  type: str
  payload: dict = {}

JobCreateRequest:
  name: str            (min length 1)
  priority: int = 0    (>= 0)
  tasks: list[TaskCreate]  (min length 1 — a job needs at least one task)

TaskResponse:
  id: UUID
  type: str
  payload: dict
  status: TaskStatus
  attempt: int
  max_retries: int
  worker_id: UUID | None

JobResponse:
  id: UUID
  name: str
  priority: int
  status: JobStatus
  created_at: datetime
  updated_at: datetime

JobDetailResponse(JobResponse):
  tasks: list[TaskResponse]
```

`POST /jobs` and `POST /jobs/{job_id}/cancel` return `JobDetailResponse`.
`GET /jobs` returns `list[JobResponse]`. `GET /jobs/{job_id}` returns
`JobDetailResponse`.

**Known documentation conflict to resolve as part of this phase:** the
example in `docs/api/rest-api.md` shows a flat task shape
(`{"type": "shell", "command": "echo Hello"}`), but the Phase 01 `Task`
domain object and the Phase 02 `tasks.payload` JSONB column (source of
truth, per `CLAUDE.md` §1 priority order) use a nested `payload: dict`.
Resolution: the API uses `{"type": "shell", "payload": {"command": "echo Hello"}}`,
matching the domain/persistence shape. `docs/api/rest-api.md`'s example will
be updated to match during implementation (small, appropriate doc
correction per `CLAUDE.md` §18 — not an architecture change).

## HTTP Status Codes

- `201 Created` — successful `POST /jobs`.
- `200 OK` — successful `GET /jobs`, `GET /jobs/{job_id}`,
  `POST /jobs/{job_id}/cancel`.
- `404 Not Found` — job does not exist (retrieval or cancellation).
- `409 Conflict` — cancellation attempted on a job whose current status has
  no `CANCELLED` transition (`InvalidTransitionError`).
- `422 Unprocessable Entity` — request body fails Pydantic validation
  (FastAPI default; no custom handling needed).

## Validation

- `name`: non-empty string.
- `priority`: integer, `>= 0` (rejects negative priorities; no upper bound
  imposed — priority ordering semantics belong to the Scheduler, Phase 05).
- `tasks`: at least one entry; each entry needs a non-empty `type`.
- `job_id` / path UUIDs: FastAPI's `UUID` path-param type gives a `422` on
  malformed UUIDs automatically.

## Service-Layer Responsibilities

`src/atlas/services/job_service.py`, `JobService`:

- `create_job(name, priority, tasks) -> Job/Task construction + persistence`
- `get_job(job_id) -> (Job, list[Task]) | None`
- `list_jobs(status: Optional[JobStatus]) -> list[Job]`
- `cancel_job(job_id) -> Job` (raises a `JobNotFoundError` or lets
  `InvalidTransitionError` propagate — route layer maps both to HTTP)

Route handlers in `src/atlas/api/routes/jobs.py` only: parse/validate the
request (via Pydantic), call one `JobService` method, map the result or a
raised domain/service error to an HTTP response. No repository or session
usage directly in route handlers.

## Repository Interaction

- Reuses `JobRepository`, `TaskRepository` from Phase 02 as-is for
  add/get/update/list_by_job/list_by_status.
- **Blocking gap identified:** `JobRepository` has no "list all jobs"
  method (only `list_by_status`), but `GET /jobs` with no filter needs one.
  This phase adds `JobRepository.list_all(session) -> list[Job]` — a small,
  additive method following the exact pattern of the existing
  `list_by_status`. This is the one Phase 02 touch-point permitted by your
  instructions ("unless the plan identifies a genuine blocking issue").
- No other Phase 01/02 changes.

## Transaction Boundaries

- `create_job`: one `session_scope()` covers the job insert and all task
  inserts — either the whole job is created or none of it is (matches
  `docs/design/consistency.md`: state transitions are atomic).
- `cancel_job`: one `session_scope()` covers loading the job, loading its
  tasks, and updating all of their statuses.
- `get_job` / `list_jobs`: one read-only `session_scope()` each (commit is a
  no-op on reads, but reuses the same helper for consistency).

## Error Handling

- `JobNotFoundError` (new, small exception in the service module) →
  `404` via a FastAPI exception handler registered in `main.py`.
- `atlas.domain.errors.InvalidTransitionError` (Phase 01, reused) →
  `409` via the same mechanism.
- No generic catch-all error handling beyond FastAPI's defaults — no new
  error-handling framework.

## API Tests

- **Service unit tests** (`tests/services/test_job_service.py`, requires
  Postgres — same skip pattern as Phase 02 integration tests): create job
  with tasks, get, list (with/without status filter), cancel happy path,
  cancel on terminal job raises `InvalidTransitionError`, cancel unknown
  job raises `JobNotFoundError`.
- **HTTP tests** (`tests/api/test_jobs.py`, using FastAPI `TestClient`,
  same Postgres skip pattern): full request/response cycle for all four
  endpoints, including the `201/200/404/409/422` status codes above.

## Acceptance Criteria

- [ ] `POST /jobs` creates a job and its tasks atomically; returns `201`
      with `JobDetailResponse`.
- [ ] `GET /jobs` returns all jobs, or jobs filtered by `status` query param.
- [ ] `GET /jobs/{job_id}` returns the job with its tasks, or `404`.
- [ ] `POST /jobs/{job_id}/cancel` transitions the job (and its non-terminal
      tasks) to `CANCELLED`, returns `200`; returns `404` for unknown job,
      `409` for a job that cannot be cancelled from its current status.
- [ ] No business logic in route handlers — verified by inspection: routes
      only call `JobService` methods.
- [ ] `src/atlas/domain/` and `src/atlas/persistence/` have no new imports
      from `atlas.api` or `atlas.services` (dependency points one way).
- [ ] All new tests pass against the Phase 00 Postgres container; skip
      cleanly when Postgres is unreachable (same as Phase 02).
- [ ] Full existing suite (Phase 00–02) still passes unmodified.

## Out of Scope

Do NOT implement in this phase:

- `GET /workers`, `GET /workers/{worker_id}`, `GET /ready` (no Worker API
  or readiness-check design defined yet — deferred to whichever phase
  introduces the Worker API surface).
- Scheduler, priority queue, dispatch (Phase 05/06) — job/task status never
  advances past `SUBMITTED`/`PENDING` from this API.
- Worker runtime, heartbeats, failure detection (Phase 04/07/08).
- Retry/recovery logic (Phase 09).
- gRPC (Phase 10).
- Authentication/authorization — `docs/api/rest-api.md` defines none, and
  `component-architecture.md` marks it "to be added later"; none added here.
- Pagination on `GET /jobs` (not required by the documented contract; add
  when job volume actually needs it).
- Optimistic concurrency / version columns (still deferred from Phase 02).
