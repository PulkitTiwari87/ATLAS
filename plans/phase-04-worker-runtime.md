# Phase 04 — Worker Runtime

## 1. Objective

Give Atlas a worker process that can register itself, execute a single task
end-to-end (receive → run → report success/failure), persist the result, and
shut down cleanly — using the Phase 01 domain model and Phase 02 persistence
layer exactly as they are. Phase 04 does **not** decide which task a worker
runs next; it only defines how a worker runs a task once told to.

## 2. Scope

- `Worker` domain object lifecycle: register, become available, execute one
  task, shut down.
- Task execution: `PENDING → ASSIGNED → RUNNING → SUCCEEDED | FAILED`,
  persisted through the existing `TaskRepository`.
- A minimal, pluggable task-execution mechanism (executor per `task.type`).
- A minimal `TaskAttemptRepository` addition (see §7) so execution results
  land in the already-migrated `task_attempts` table.
- Unit + integration tests for all of the above.

Not in scope: anything that decides *which* task a worker should run next,
or *whether* a failed task should run again. See §15.

## 3. Worker Architecture

- **Worker process/runtime**: `src/atlas/worker/runtime.py`, a
  `WorkerRuntime` class. One `WorkerRuntime` instance = one `Worker` row =
  one OS process in a real deployment, but nothing in this phase requires
  multiple processes to exercise it — tests drive it in-process.
- **Worker identity**: built from `socket.gethostname()` and
  `f"{hostname}:{os.getpid()}"` as `address`. No new config is required;
  `address` is descriptive metadata only in this phase — it becomes a real
  dialable endpoint once Phase 10 gives workers a gRPC server to listen on.
- **Worker registration**: `WorkerRuntime.register()` creates a `Worker`
  domain object (`status=REGISTERING`, the Phase 01 default), persists it
  via `WorkerRepository.add`, then immediately calls
  `worker.transition_to(AVAILABLE)` + `WorkerRepository.update` — matching
  `docs/design/worker-lifecycle.md`'s `REGISTERING → AVAILABLE` edge. No
  separate "registration API" — registration is a local call the runtime
  makes to itself at startup (see §6 for why).
- **Worker lifecycle / availability**: tracked purely through the existing
  `WorkerStatus` enum and its `_ALLOWED_TRANSITIONS` table (Phase 01,
  unchanged). `AVAILABLE` ↔ `BUSY` while idle vs. executing a task.
- **Worker shutdown**: see §11.
- **Worker task execution / result reporting**: see §4.

## 4. Task Execution Model

`WorkerRuntime.execute_task(task_id: UUID) -> Task`:

1. **Receive**: the caller (a test today; the Phase 06 Dispatcher tomorrow)
   passes a `task_id` directly to this method — see §6.
2. **Assign** (one transaction): load the `Task`; `task.transition_to(ASSIGNED)`;
   set `task.worker_id = self.worker.id`; `TaskRepository.update`.
   `self.worker.transition_to(BUSY)`; `WorkerRepository.update`.
3. **Start execution** (one transaction): `task.transition_to(RUNNING)`;
   `task.attempt += 1`; `TaskRepository.update`. Commit and close this
   transaction *before* running the task body — a shell command or user
   code must never run while holding an open PostgreSQL transaction.
4. **Execute**: look up an executor function by `task.type` in a small
   in-process registry (`{"shell": _run_shell}` to start, matching the
   `docs/api/rest-api.md` example payload). The executor receives the
   `Task` and returns a `dict` result or raises.
5. **Report success**: executor returned normally →
   `task.transition_to(SUCCEEDED)`, `TaskRepository.update`, write a
   `task_attempts` row (`result=<executor output>`, `error=None`).
6. **Report failure**: executor raised → catch `Exception` (not narrower —
   task code is arbitrary and must never crash the runtime),
   `task.transition_to(FAILED)`, `TaskRepository.update`, write a
   `task_attempts` row (`result=None`, `error=str(exc)`).
7. **Release**: `self.worker.transition_to(AVAILABLE)`,
   `WorkerRepository.update`. Steps 5–7 happen in one transaction.

No retry, no requeue, no reassignment — a `FAILED` task simply stays
`FAILED` at the end of this phase (`docs/design/retries.md`'s
`FAILED → RETRYING` edge is Phase 09's to drive).

## 5. Worker Concurrency

**One task at a time, synchronous, no threads/processes/asyncio.**

Justification: `WorkerStatus` only has binary `AVAILABLE`/`BUSY` states —
there is no "partial capacity" state in the Phase 01 state machine, so
building a worker that runs N concurrent tasks would either violate that
state machine or require inventing new states (explicitly disallowed).
Running multiple tasks concurrently, if ever needed, means running multiple
`WorkerRuntime` processes — an operational choice, not a code change.
`execute_task` is a plain blocking method call; no `threading`,
`multiprocessing`, or `asyncio` in this phase.

## 6. Worker ↔ Atlas Communication

**Mechanism for Phase 04: direct in-process Python method call**
(`WorkerRuntime.execute_task(task_id)`), with PostgreSQL as the only shared
state between "control plane" and "worker" (already true per ADR-002).

Why this is the right choice for Phase 04, not a shortcut around it:
- `docs/architecture/communication.md` names gRPC as the eventual
  worker↔control-plane protocol, but that is explicitly Phase 10
  (`plans/roadmap.md`). Phase 06 (Dispatch) — which decides *which* worker
  gets *which* task — doesn't exist yet either. Building a network protocol
  in Phase 04 for a caller (the Dispatcher) that doesn't exist yet would be
  exactly the kind of premature architecture `CLAUDE.md` §20 forbids.
- **How tasks are delivered**: a caller invokes
  `WorkerRuntime.execute_task(task_id)` directly. In Phase 04 that caller is
  a test. From Phase 06 onward, the same method is what the Dispatcher
  calls. From Phase 10 onward, this method becomes the handler behind a
  gRPC `AssignTask` RPC — the method signature (`task_id in, Task out`) is
  designed to survive that transport change unchanged.
- **How results are returned**: the method's return value (an updated
  `Task`) plus the durable Postgres row are both available the moment
  `execute_task` returns — no polling or callback needed within this phase.
  Once dispatch is remote (Phase 10), "results are returned" becomes a
  gRPC response carrying the same data.
- **How worker registration works**: local `register()` call at process
  startup, writing directly to `workers` via `WorkerRepository` (§3). This
  doesn't change shape when workers become remote — registration was
  always "worker writes its own row," never a control-plane-initiated
  handshake.
- **Evolution path to Phase 10**: nothing here is thrown away later.
  `WorkerRuntime` keeps its exact same public methods
  (`register`, `execute_task`, `shutdown`); Phase 10 adds a gRPC service
  that calls them from the network instead of from Python. This phase
  produces the callee; Phase 10 produces the caller's new transport.

## 7. Persistence Interaction

Reused as-is, no changes:
- `WorkerRepository` (`add`, `get`, `update`) — Phase 02.
- `TaskRepository` (`add`, `get`, `update`) — Phase 02.

**One additive, minimal repository required** (same pattern as
`JobRepository.list_all` in Phase 03):
- `TaskAttemptRepository.add(session, attempt: TaskAttempt) -> TaskAttempt`
  — the `task_attempts` table and `TaskAttemptRow` ORM model already exist
  (Phase 02 migration), created specifically for this purpose and marked
  "no repository yet" pending a real caller. Phase 04 is that caller.
  A minimal `TaskAttempt` domain object is added to `atlas.domain` (id,
  task_id, worker_id, attempt_number, started_at, finished_at, result,
  error) — a plain data holder, no state machine (attempts don't transition
  between states; only the parent `Task` does).
- No other Phase 02 changes. `JobRepository` is not touched or used by the
  worker runtime — see §18 for why job-status aggregation is explicitly not
  this phase's job.

## 8. Worker State Transitions

Exactly the Phase 01 `WorkerStatus` machine, no new states:

```
REGISTERING → AVAILABLE      (register())
AVAILABLE   → BUSY            (execute_task() start)
BUSY        → AVAILABLE       (execute_task() end, any outcome)
AVAILABLE   → DRAINING        (shutdown() when idle)
DRAINING    → DEAD            (shutdown() completes)
```

`BUSY → UNHEALTHY` and `AVAILABLE → UNHEALTHY` are **not** driven by this
phase — those belong to the Heartbeat/Failure Detector (Phase 07/08).

## 9. Task State Transitions

Exactly the Phase 01 `TaskStatus` machine, no new states:

```
PENDING  → ASSIGNED    (execute_task() step 2)
ASSIGNED → RUNNING     (execute_task() step 3)
RUNNING  → SUCCEEDED   (executor returns)
RUNNING  → FAILED      (executor raises)
```

`FAILED → RETRYING → PENDING` (Phase 09) and any `CANCELLED` edge (already
owned by `JobService.cancel_job`, Phase 03) are not driven by this phase.

## 10. Failure Boundary

- **Task execution failure** (the executor raises, e.g. a shell command
  exits non-zero or throws): caught inside `execute_task`, task →
  `FAILED`, worker → back to `AVAILABLE`. This is normal, expected, and
  fully handled by this phase.
- **Worker failure** (the process crashes, is killed, or hangs mid-task):
  explicitly **not** handled here. If `execute_task` never returns because
  the process died, the task is left `RUNNING` and the worker is left
  `BUSY` in PostgreSQL forever, by design — detecting and recovering from
  that is the Heartbeat Manager / Failure Detector / Recovery Manager's job
  (Phase 07–09). Phase 04 must not add heartbeat sending, timeout
  detection, or any self-healing for this case.

## 11. Graceful Shutdown

`WorkerRuntime.shutdown()`:

1. Set an internal `_draining = True` flag. Any subsequent `execute_task`
   call raises `WorkerDrainingError` (new, small exception) instead of
   accepting work — "stop accepting new work."
2. Because execution is synchronous and single-task (§5), there is never an
   in-flight task to wait for when `shutdown()` runs: either the worker is
   `AVAILABLE` (nothing to finish) or a caller is still blocked inside a
   currently-running `execute_task` call (which will finish naturally,
   return the worker to `AVAILABLE`, and *then* see `_draining` on its next
   call). No cross-thread coordination is needed.
3. `worker.transition_to(DRAINING)` (only valid from `AVAILABLE`, per §8 —
   this is why shutdown never needs a `BUSY → DRAINING` edge).
4. `worker.transition_to(DEAD)`, `WorkerRepository.update`.

## 12. API/Service Interaction

None required. Phase 04 adds no REST endpoints and no FastAPI routes.
`WorkerRuntime` plays the "service layer" role for worker-side operations
itself (it already sits directly between execution logic and the
repositories, exactly where `JobService` sits for the API); adding a
separate `WorkerService` underneath it with no other caller would be an
unnecessary abstraction. `GET /workers`, `GET /workers/{id}` (contract-only
in `docs/api/rest-api.md`) remain out of scope, same as Phase 03 flagged.

## 13. Testing Strategy

**Unit tests** (`tests/worker/test_runtime.py`, no database — construct
domain objects directly, fake repositories or a lightweight in-memory
double for `WorkerRepository`/`TaskRepository` if needed to isolate from
Postgres; prefer real repositories against Postgres where practical and
push only the tightest logic-only cases into true unit tests):
- registration transitions `REGISTERING → AVAILABLE`.
- shutdown from `AVAILABLE` transitions `DRAINING → DEAD`.
- `execute_task` after `shutdown()` raises `WorkerDrainingError`.
- unknown `task_id` raises a clear error (`TaskNotFoundError`).

**Integration tests** (`tests/worker/test_task_execution.py`, real
Postgres, same skip-if-unreachable pattern as Phases 02/03):
- `register()` persists a `Worker` row queryable via `WorkerRepository`.
- successful task: `execute_task` on a `shell` task with a command that
  exits 0 → task `SUCCEEDED`, worker back to `AVAILABLE`, a `task_attempts`
  row with `result` set and `error` NULL.
- failed task: command that exits non-zero or unknown `task.type` →
  task `FAILED`, worker back to `AVAILABLE`, a `task_attempts` row with
  `error` set.
- task exception mid-executor (e.g. executor raises `ValueError`) is
  caught and reported as `FAILED`, never propagates out of `execute_task`.
- attempting to execute an already-`ASSIGNED`/`RUNNING` task a second time
  raises `InvalidTransitionError` (Phase 01 guarantee, exercised not
  reimplemented).
- `shutdown()` persists `DEAD` status; a `WorkerRepository.get` after
  shutdown confirms it.

No heartbeat, failure-detection, or recovery tests — explicitly out of
scope (§15).

## 14. Acceptance Criteria

- [ ] A `WorkerRuntime` can be constructed and `register()`s a `Worker` row
      that reaches `AVAILABLE` in PostgreSQL.
- [ ] `execute_task(task_id)` moves a `PENDING` task through
      `ASSIGNED → RUNNING → SUCCEEDED` (or `FAILED`) and returns the final
      `Task`.
- [ ] A successful execution persists a `task_attempts` row with a result
      and no error.
- [ ] A failed execution (non-zero exit, executor exception, or unknown
      task type) persists a `task_attempts` row with an error, and the
      task ends in `FAILED` — not left `RUNNING`.
- [ ] The worker returns to `AVAILABLE` after any task outcome.
- [ ] `shutdown()` takes a worker from `AVAILABLE` to `DEAD` and rejects
      further `execute_task` calls.
- [ ] No scheduler, dispatcher, heartbeat, retry, recovery, or gRPC code is
      introduced.
- [ ] `src/atlas/domain/` gains only the plain `TaskAttempt` data object —
      no new state machine, no new imports from `atlas.persistence` or
      `atlas.worker`.
- [ ] All existing Phase 00–03 tests still pass unmodified.

## 15. Explicit Out-of-Scope

- Priority scheduling / the Scheduler component (Phase 05).
- Dispatch orchestration — choosing which worker runs which task
  (Phase 06).
- Heartbeats (Phase 07).
- Failure detection / worker death detection (Phase 08).
- Retries / retry policy / `FAILED → RETRYING` (Phase 09).
- Task reassignment and recovery (Phase 09).
- gRPC implementation (Phase 10).
- Observability/metrics (Phase 11).
- Authentication (not in the current API design at all).
- Distributed locking.
- Worker autoscaling or multi-task concurrency per worker (§5).
- Job-status aggregation from task outcomes (see §18 — flagged as an
  open assumption, not silently implemented).

## 16. Files Expected to Change

New:
- `src/atlas/worker/__init__.py`
- `src/atlas/worker/runtime.py` — `WorkerRuntime`, the executor registry,
  `_run_shell` executor.
- `src/atlas/worker/errors.py` — `WorkerDrainingError`, `TaskNotFoundError`.
- `src/atlas/domain/task_attempt.py` — plain `TaskAttempt` data object
  (added to `src/atlas/domain/__init__.py` exports).
- `tests/worker/test_runtime.py`
- `tests/worker/test_task_execution.py`

Modified (additive only):
- `src/atlas/persistence/repositories.py` — add `TaskAttemptRepository` +
  its domain↔row mapping functions, following the existing pattern exactly.
- `src/atlas/domain/__init__.py` — export `TaskAttempt`.

Not touched: `src/atlas/domain/job.py`, `task.py`, `worker.py`,
`_transitions.py`, `errors.py`; `src/atlas/persistence/models.py`,
`db.py`; `src/atlas/api/*`; `src/atlas/services/*`; `src/atlas/main.py`.

`docker-compose.yml` is deliberately **not** touched in this phase — see
§18, "worker-1 in Docker Compose."

## 17. Dependencies

None. `subprocess` (stdlib) for the `shell` executor, `socket`/`os`/`uuid`
(stdlib) for identity. No Redis/Kafka/RabbitMQ/Celery/Kubernetes, no new
PyPI package.

## 18. Risks and Design Decisions

- **Task ownership / duplicate execution**: guarded entirely by reusing
  the Phase 01 state machine — `task.transition_to(ASSIGNED)` raises
  `InvalidTransitionError` if the task isn't `PENDING`, so two callers
  racing to assign the same task can't both succeed. No new locking is
  added; this is the existing domain guarantee doing its job.
- **Worker capacity race**: same mechanism — `worker.transition_to(BUSY)`
  raises if the worker isn't `AVAILABLE`, preventing double-assignment to
  one worker instance.
- **Concurrent state changes**: each step in §4 is its own short
  transaction (per `docs/design/consistency.md`), so no transaction is held
  open across the (potentially slow, arbitrary-duration) task execution
  itself. This is a deliberate deviation from "one transaction per
  operation" for correctness reasons, called out explicitly here rather
  than left implicit.
- **Worker shutdown mid-task**: not possible to observe in this phase's
  model — execution is synchronous, so `shutdown()` can only run either
  before or after a call to `execute_task`, never during one on the same
  runtime instance (§11).
- **Late task results**: out of scope — a task result written after the
  process has, from the outside, been considered dead is a Phase 08/09
  concern (this phase has no notion of "dead," only synchronous
  completion).
- **Interaction with PostgreSQL**: no new connection-pooling or
  transaction-isolation behavior; reuses `session_scope()` exactly as
  Phase 02/03 do.
- **Open assumption — job-status aggregation**: should a `Task` reaching
  `SUCCEEDED`/`FAILED` ever cause its parent `Job` to move toward
  `RUNNING`/`COMPLETED`/`FAILED`? `docs/design/job-lifecycle.md` says job
  transitions are "driven by task state changes," which could mean this is
  Phase 04's job. This plan deliberately does **not** implement that
  aggregation — it doesn't fit "worker execution" scope, and doing it
  correctly needs to consider all of a job's tasks together (more natural
  for the Job Manager / a later Scheduler/Dispatch-adjacent phase, once
  there's an actual multi-task job flowing through the system). **Flagging
  for your confirmation before implementation.**
- **Open assumption — shell executor safety**: `_run_shell` runs
  `payload["command"]` via `subprocess`, matching the documented example
  payload directly, with no sandboxing, resource limits, or timeout
  enforcement in this phase. That hardening is deferred to Phase 13
  per `plans/roadmap.md`. **Flagging so this tradeoff is explicit, not
  silently shipped.**
- **worker-1 in Docker Compose**: now that `src/atlas/worker/runtime.py`
  will exist, Phase 00's docker-compose comment ("added once modules
  exist") is technically satisfiable — but a real `worker-1` service needs
  something to call `execute_task` with actual task IDs (i.e. a
  Dispatcher), which still doesn't exist until Phase 06. Recommendation:
  leave `docker-compose.yml` as Postgres-only until Phase 06, to avoid a
  service that starts but does nothing.

## 19. Implementation Sequence

1. **Domain**: add `TaskAttempt` (plain data object) to `atlas.domain`.
2. **Persistence**: add `TaskAttemptRepository` (mapping + `add`) to
   `atlas.persistence.repositories`.
3. **Worker foundation**: `src/atlas/worker/errors.py`
   (`WorkerDrainingError`, `TaskNotFoundError`); `WorkerRuntime.__init__`
   building worker identity.
4. **Registration**: `WorkerRuntime.register()` / verify
   `REGISTERING → AVAILABLE`.
5. **Task execution**: `execute_task()` steps 2–4 (assign, start, execute)
   with the `shell` executor.
6. **Result persistence**: steps 5–7 (success/failure reporting,
   `task_attempts` write, release worker).
7. **Shutdown**: `shutdown()` and the draining guard.
8. **Tests**: unit tests (§13) then integration tests (§13), run against
   the Phase 00 Postgres container.
9. **Full suite verification**: confirm Phase 00–03 tests are unaffected.

## 20. Definition of Done

Phase 04 is complete when every checkbox in §14 is checked, the tests in
§13 pass against the running Postgres container and skip cleanly without
it (matching the Phase 02/03 pattern), the full existing test suite still
passes, and no file outside §16's list has been modified.
