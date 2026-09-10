# Phase 06 — Dispatch

## 1. Objective

Turn a Phase 05 scheduling proposal into an actual task execution, without
duplicating any ownership logic that Phase 04 already implements correctly.

```
Scheduler   (Phase 05, unchanged): decides what should run — an ordered,
                                    ephemeral list of (Task, Worker) pairs.
Dispatch    (Phase 06, this plan): takes one pair, finds the worker's
                                    in-process runtime, and hands the task
                                    to it. Delivers; does not decide, does
                                    not execute.
Worker      (Phase 04, unchanged): WorkerRuntime.execute_task() performs
                                    the actual claim (PENDING -> ASSIGNED),
                                    execution, and result persistence.
```

Phase 06's entire job is the middle arrow: given a proposal, get the task
to the right `WorkerRuntime` and interpret the outcome. It introduces no
new claim mechanism, no new execution mechanism, and no new persistence
mechanism — see §3 and §12 for why that's a deliberate simplification, not
an oversight.

## 2. Current Phase 05 Behavior (verified against the actual code)

`src/atlas/scheduler/scheduler.py`, `Scheduler.run_cycle()`:
1. `_load_runnable_tasks()` — all `PENDING` tasks.
2. `_advance_submitted_jobs(runnable_tasks)` — for each distinct job among
   them, `SUBMITTED → QUEUED` if still `SUBMITTED` (the only DB write
   Scheduler performs).
3. `_order_by_priority(runnable_tasks)` — sorted by
   `(-job.priority, task.created_at)`.
4. `_load_available_workers()` — all `AVAILABLE` workers, sorted by
   `worker.id`.
5. Returns `list(zip(ordered_tasks, available_workers))` — a plain Python
   list of `(Task, Worker)` tuples, **never persisted**, **never
   re-validated after being returned**.

This plan builds on that exactly as-is. No change to
`src/atlas/scheduler/scheduler.py` is needed or made (confirmed by the end
of this plan — see §21).

## 3. Phase 04 / Phase 06 Ownership Conflict — Resolution

Verified from `src/atlas/worker/runtime.py` (Phase 04, unchanged since it
was written):

- `execute_task(task_id)` → `_assign()`: `task.transition_to(ASSIGNED)`,
  sets `task.worker_id`, `worker.transition_to(BUSY)` — one transaction.
- → `_start()`: `task.transition_to(RUNNING)`, `task.attempt += 1`.
- → executes, then `_finish()`: `task.transition_to(SUCCEEDED|FAILED)`,
  writes a `TaskAttempt`, `worker.transition_to(AVAILABLE)`.

**Decision: Phase 04 keeps 100% ownership of every one of these
transitions. Phase 06 (Dispatch) never calls `task.transition_to(...)` or
`worker.transition_to(...)` and never writes `Task.worker_id` directly.**
Dispatch's only job is to locate the right `WorkerRuntime` instance for a
proposed worker and call its existing `execute_task(task.id)` — the exact
seam Phase 04's own docstring already names: *"execute_task() is a direct
in-process call today. It is the seam a future Dispatcher (Phase 06) ...
calls into."*

Answering each required question directly:
- **Who claims the task?** `WorkerRuntime._assign()`, unchanged.
- **Who assigns `worker_id`?** `WorkerRuntime._assign()`, unchanged.
- **Who changes worker state?** `WorkerRuntime`, unchanged
  (`_assign`/`_finish`).
- **Who changes `TaskStatus` to `ASSIGNED`?** `WorkerRuntime._assign()`,
  unchanged.
- **Who starts execution?** `WorkerRuntime.execute_task()`, unchanged —
  Dispatch calls it but does not reimplement any part of it.
- **How is duplicate dispatch prevented?** The exact same mechanism
  Phase 04 already has and already tests
  (`test_double_assignment_raises_invalid_transition`):
  `task.transition_to(ASSIGNED)` raises `InvalidTransitionError` if the
  task isn't `PENDING`. Two dispatches racing on the same task_id can only
  ever have one succeed.
- **What happens if dispatch fails before execution?** See §11.A — no
  state was touched, task stays exactly as it was; safe to drop the
  proposal.
- **What happens if the worker rejects the task?** In this phase "reject"
  means `execute_task` raises `InvalidTransitionError` (task/worker no
  longer eligible) or `WorkerDrainingError` (worker is shutting down) —
  both are caught by Dispatch and treated as a non-fatal skip (§9, §11.A).
- **What happens if the worker disappears?** Not detectable in this phase
  — no heartbeats exist yet. See §8/§11.C: explicitly deferred to
  Phase 07–09.

**No change to `src/atlas/worker/runtime.py` is required.** This
resolution was chosen specifically *because* it requires zero Phase 04
changes — the alternative (Phase 05/06 owning the claim, Phase 04 only
consuming already-`ASSIGNED` tasks) would require splitting
`execute_task()` into a separate claim step and an execute step, which is
exactly the kind of speculative Phase 04 modification the brief says not
to make without a genuine blocking conflict. There is no such conflict
here — reusing `execute_task()` as one atomic call is sufficient.

## 4. Dispatch Mechanism

**Chosen: a small in-process worker registry + direct method call.** No
Redis, no message broker, no gRPC.

- `src/atlas/dispatch/registry.py`, `WorkerRegistry`: a plain
  `Dict[UUID, WorkerRuntime]` behind `register(runtime)`,
  `unregister(worker_id)`, `get(worker_id) -> Optional[WorkerRuntime]`.
  Whatever code starts a worker (today: a test; eventually: a worker
  process's `__main__`) calls `registry.register(runtime)` after
  `runtime.register()` succeeds. **This is a Dispatch-side concern only —
  `WorkerRuntime` itself is never modified to know about a registry.**
- `src/atlas/dispatch/dispatcher.py`, `Dispatcher.dispatch(task, worker)`:
  looks up the runtime via the registry; if found, calls
  `runtime.execute_task(task.id)` directly (a normal Python method call,
  synchronous, in-process).

Why this is right for Phase 06:
- **Simple**: no new transport, no serialization, no networking.
- **Testable**: a test can register a real `WorkerRuntime` and assert on
  `Dispatcher.dispatch()`'s outcome directly.
- **Replaceable**: `Dispatcher.dispatch(task, worker)`'s signature does
  not change when Phase 10 arrives (§24) — only what's behind
  `registry.get(worker.id)` changes, from "a local object" to "a gRPC
  stub for `worker.address`."
- `docs/architecture/communication.md` names gRPC as the eventual
  worker↔control-plane protocol, but explicitly for Phase 10
  (`plans/roadmap.md`) — building it now, before a real multi-process
  worker deployment exists to justify it, would be exactly the premature
  architecture `CLAUDE.md` §20 forbids.

## 5. Task Delivery

- **Dispatcher input**: one `(Task, Worker)` pair from a Scheduler
  proposal (or a full proposal list, for `dispatch_all` — §10).
- **Task identity**: `task.id`, passed to `execute_task`.
- **Worker identity**: `worker.id`, used only to look up the runtime in
  `WorkerRegistry` — Dispatch never touches `worker.status` itself.
- **Payload retrieval**: not Dispatch's concern — `execute_task()`
  re-fetches the task (and its `payload`) fresh from the database itself
  (`TaskRepository.get`) as its first step. Dispatch never reads or
  passes `task.payload`.
- **Worker invocation**: `runtime.execute_task(task.id)` — a plain,
  blocking method call.
- **Execution lifecycle / result handling / error handling**: entirely
  `WorkerRuntime`'s, unchanged (§3, §13). Dispatch only interprets the
  return value or the exception it raises — see §11.

**No new executor logic is added.** Dispatch contains no `subprocess`
call, no executor registry, nothing that resembles task execution.

## 6. Task State Machine

Unchanged from Phase 01/04 — no new transitions, no new states, and (per
§3) Dispatch itself calls `transition_to` zero times:

```
PENDING  --(WorkerRuntime._assign, unchanged)-->     ASSIGNED
ASSIGNED --(WorkerRuntime._start, unchanged)-->       RUNNING
RUNNING  --(WorkerRuntime._finish, unchanged)-->      SUCCEEDED | FAILED
```

`RETRYING` is not touched (Phase 09, as before). No modification to
`src/atlas/domain/task.py` — no blocking inconsistency was found.

## 7. Worker State Machine

Unchanged from Phase 01/04 — binary `AVAILABLE`/`BUSY`, both transitions
owned entirely by `WorkerRuntime`:

```
AVAILABLE --(WorkerRuntime._assign, unchanged)--> BUSY
BUSY      --(WorkerRuntime._finish, unchanged)--> AVAILABLE
```

No capacity field, no partial availability, no new worker states. No
modification to `src/atlas/domain/worker.py`.

## 8. At-Least-Once Semantics

Per ADR-003, Atlas is at-least-once, never exactly-once — Phase 06 does
not change that.

- **Dispatcher crash before calling `execute_task`**: nothing happened;
  the task is still `PENDING`. The next Scheduler cycle re-proposes it.
  Safe, no duplicate.
- **Dispatcher crash *during* `execute_task`**: because Phase 06 keeps
  everything in one process (§4 — no real transport boundary yet), this
  is indistinguishable from a worker crash in this phase. The task is
  left `RUNNING`/`ASSIGNED` in PostgreSQL with no automatic recovery —
  explicitly Phase 07–09's job (§11.C). A genuinely separate
  dispatcher-vs-worker crash boundary only exists once Phase 10 puts
  workers in their own process behind a real transport.
- **Execution starting before state persistence / vice versa**: not
  possible to get wrong here — `execute_task()`'s own transaction
  ordering (claim, commit; start, commit; *then* execute; then persist
  result, commit) already guarantees the DB reflects `ASSIGNED`/`RUNNING`
  before the executor runs, and the result is durable before the method
  returns. Phase 06 adds no new ordering to reason about.
  execute_task's design already exists, and is not being modified.
- **Duplicate dispatch**: prevented by `InvalidTransitionError`, §3.
- **Stale scheduling proposals**: §9.
- **Late worker results**: not a concept that exists yet — `execute_task`
  is synchronous, so "the result" and "the return of the call" are the
  same event. This becomes a real question only once Phase 10 makes
  dispatch asynchronous over a network; not solved here.

No two-phase commit, no distributed transaction — none needed.

## 9. Stale Scheduler Proposals

A proposal is treated as **advice, not a claim** (established in Phase 05
and unchanged here). Dispatch does **not** pre-check "is this task still
PENDING?" before acting — a check-then-act pattern would itself be racy
(TOCTOU). Instead, Dispatch just attempts the call and lets Phase 04's
existing atomic guards be the single source of truth:

| Staleness case | What happens |
|---|---|
| Task no longer `PENDING` | `execute_task` raises `InvalidTransitionError` inside `_assign` — caught, treated as a dispatch failure (§11.A), proposal dropped. |
| Worker no longer `AVAILABLE` | Same — `worker.transition_to(BUSY)` raises `InvalidTransitionError` inside `_assign`. |
| Task already assigned (to this or another worker) | Same as "no longer PENDING." |
| Worker is draining | `execute_task` raises `WorkerDrainingError` (checked first, before `_assign` even runs) — caught, treated the same way. |
| Worker has disappeared (never registered / already unregistered) | `WorkerRegistry.get(worker.id)` returns `None` — Dispatch never calls `execute_task` at all; treated as a dispatch failure. |
| Another dispatch already claimed the task | Same as "no longer PENDING" — whichever call reaches `_assign` first wins; the loser gets `InvalidTransitionError`. |

## 10. Concurrency

- **Task/worker ownership races**: fully covered by Phase 04's existing
  state-machine guards (§3, §9) — Phase 06 adds no locking of its own
  because none is needed.
- **Dispatcher concurrency (multiple proposals in one cycle)**: chosen
  approach is **sequential** — `dispatch_all(proposals)` calls
  `dispatch(task, worker)` for each proposal in order, one at a time.
  Trade-off, stated plainly: since `execute_task()` blocks until the task
  finishes, only the *first* proposal in a cycle actually executes
  concurrently with anything; a second `AVAILABLE` worker proposed in the
  same cycle won't be used until a *later* cycle (once the loop reaches
  it again after the first dispatch returns). Multiple workers still all
  get used over time, just not within a single cycle. This keeps Phase 06
  free of any new concurrency primitive (`threading`, `asyncio`) and
  matches "do not add locks/concurrency unless actually necessary" —
  nothing in the acceptance criteria requires within-cycle parallelism.
  **Flagging this explicitly for your confirmation** — the alternative
  (one `threading.Thread` per proposal in `dispatch_all`, joined before
  returning) is a small, contained change if within-cycle parallel
  worker utilization is actually required now; noted here rather than
  decided unilaterally.
- **Scheduler cycle overlap**: unaffected — Scheduler still writes
  nothing but the idempotent `QUEUED` move (Phase 05, unchanged).
- **Two dispatch attempts for the same task**: §9 — the second gets
  `InvalidTransitionError`.
- **Two tasks targeting the same worker**: cannot happen from a single
  cycle's proposals (Scheduler pairs each worker with at most one task,
  §2); across cycles, the second attempt on a still-`BUSY` worker gets
  `InvalidTransitionError` the same way.

No PostgreSQL advisory locks, no `SELECT ... FOR UPDATE` beyond what
`session_scope()` already does implicitly per-transaction, no distributed
locking infrastructure.

## 11. Dispatch Failure vs. Task Execution Failure vs. Worker Failure

**A. Dispatch failure** — proposal could not even be attempted or was
rejected before execution began:
- Worker not found in `WorkerRegistry` (never registered / unregistered).
- `execute_task` raised `WorkerDrainingError`, `TaskNotFoundError`, or
  `InvalidTransitionError` before any execution occurred.
Handling: caught by `Dispatcher.dispatch`, logged, returned as a
`DispatchResult(status="skipped", reason=...)` (or similar — see §21).
**No `TaskAttempt` is written for these** (`WorkerRuntime` only writes one
once the executor actually ran, unchanged) — nothing to persist for a
dispatch that never reached execution. The task's real DB status is left
exactly as `execute_task`'s failed transition left it (i.e., untouched).

**B. Task execution failure** — the worker received the task and the
executor itself failed (non-zero shell exit, executor exception, unknown
task type): **already fully handled by Phase 04**, unchanged.
`execute_task` returns normally with `task.status == FAILED` and a
`TaskAttempt` recorded. Dispatch does nothing extra — it just relays
`execute_task`'s ordinary return value as a successful *dispatch* (the
delivery succeeded; the task itself failed, which is a different, already
-handled outcome).

**C. Worker failure** (crashes, hangs, disappears mid-task): **out of
scope**, explicitly not implemented. Phase 06 has no way to observe this
(no heartbeats exist). If it happens, the task is left `RUNNING`/`ASSIGNED`
indefinitely — a known, accepted gap, owned by Phase 07 (heartbeats),
Phase 08 (failure detection), and Phase 09 (recovery).

## 12. Persistence

**No repository changes are required. Dispatch uses no repository
directly, and opens no database session/transaction of its own.**

This is deliberate, not an oversight: every piece of state Dispatch might
otherwise need to touch is already owned and correctly transacted by
`WorkerRuntime` (§3, §8). Adding a second write path — even a read-only
pre-check — would either duplicate logic Phase 04 already has or
introduce a TOCTOU race Phase 04's atomic transitions already avoid (§9).
`WorkerRegistry` (§4) is in-memory only, not a persistence abstraction.

Transaction boundaries, restated for completeness (all inside
`WorkerRuntime`, unchanged): claim (`_assign`, one transaction) → start
(`_start`, one transaction) → **execute outside any transaction** →
result persistence (`_finish`, one transaction). Dispatch never holds a
transaction open, because it never opens one.

## 13. Result Handling

`TaskAttemptRepository`, `Task.status`, `Worker.status`: all written
exactly once, inside `WorkerRuntime._finish()`, unchanged from Phase 04.
Dispatch reads none of this directly — it only receives back whatever
`execute_task()` returns (the final `Task`) or raises. **Single owner for
execution result persistence remains `WorkerRuntime`**, confirmed, not
duplicated.

## 14. Scheduler Integration

Chosen: **a small, separate composition class owns the loop; neither
Scheduler nor Dispatcher depends on the other directly.**

- `src/atlas/dispatch/loop.py`, `DispatchLoop(scheduler, dispatcher)`:
  - `run_cycle()`: `proposals = self._scheduler.run_cycle()`; then
    `self._dispatcher.dispatch_all(proposals)`.
  - `run_forever(poll_interval_seconds=1.0)` / `stop()`: same shape as
    `Scheduler.run_forever`/`stop` (§16 — reuse the pattern, don't invent
    a new one).

Why not "Scheduler directly invokes Dispatcher": would require modifying
`src/atlas/scheduler/scheduler.py` to take a Dispatcher dependency,
touching a file this plan otherwise leaves alone, and would blur
Scheduler's single responsibility (deciding) with Dispatcher's (acting).

Why not "Dispatcher independently consumes proposals" (e.g. Dispatcher
holds a `Scheduler` reference itself): same coupling problem in the other
direction, and makes `Dispatcher.dispatch`/`dispatch_all` harder to unit
test in isolation from Scheduler.

`DispatchLoop` is the "minimal control-plane composition," not a
framework — it has no responsibilities beyond calling the two existing
pieces in order. **No change to `src/atlas/scheduler/scheduler.py`.**

## 15. Worker Registration

Inspected: `WorkerRuntime.register()` (Phase 04) persists a `Worker` row
and transitions it to `AVAILABLE` — it has no concept of any registry and
is not modified here (§3, §4).

`WorkerRegistry` (§4) is the minimal in-process lookup Dispatch needs to
turn a `worker_id` (from a proposal) into a callable `WorkerRuntime`
object. It is **not** service discovery, not a heartbeat mechanism, not
persisted — a plain dict wrapped in a small class for a clear interface.
**Explicitly a temporary implementation boundary**: Phase 10 replaces
`WorkerRegistry.get(worker_id) -> WorkerRuntime` with something that
resolves `worker.address` to a gRPC channel/stub instead — the rest of
`Dispatcher.dispatch()` does not need to change shape when that happens
(§24).

## 16. Graceful Shutdown

- `DispatchLoop.stop()`: same pattern as `Scheduler.stop()` — sets a flag,
  checked right after each `run_cycle()`, loop exits without starting
  another cycle. No mid-cycle interruption (a cycle's dispatch calls are
  synchronous and, per §10, sequential — nothing to interrupt safely mid-
  flight without risking an inconsistent claim).
- **Currently-dispatching tasks**: because dispatch is sequential and
  synchronous (§10), "shutdown while dispatching" means, at most, the
  in-flight `execute_task()` call finishes normally before `stop()` takes
  effect on the *next* cycle — never a half-dispatched task.
- **Worker shutdown**: unrelated and unchanged — `WorkerRuntime.shutdown()`
  (Phase 04) is a separate concern; `DispatchLoop` doesn't call it and
  doesn't need to know about it.
- **Scheduler shutdown ordering**: `DispatchLoop` owns both cycle calls in
  one `run_cycle()`, so there is no separate "stop the scheduler, then
  stop the dispatcher" ordering question — one `stop()` on `DispatchLoop`
  covers both. `Scheduler.run_forever()`/`stop()` remain available for
  standalone use (e.g. existing Phase 05 tests) but are not invoked by
  `DispatchLoop`, which calls `Scheduler.run_cycle()` directly instead.

## 17. Job Status

**Unchanged and untouched.** Dispatch has no repository access (§12),
so it cannot touch `Job` status even incidentally. `SUBMITTED → QUEUED`
remains exclusively Scheduler's (Phase 05); `QUEUED → RUNNING/COMPLETED/
FAILED` remain unimplemented, exactly as before. No architectural
justification was found requiring a Phase 06 change here — confirmed
during this inspection, not assumed.

## 18. Testing Strategy

**Unit tests** (`tests/dispatch/test_dispatcher.py`, no database — use a
fake/minimal `WorkerRuntime`-shaped double where isolation matters, and
real domain objects throughout):
- `WorkerRegistry.register`/`get`/`unregister` behave as a plain map.
- `dispatch()` with an unregistered worker returns a "skipped" result and
  calls nothing on any runtime.
- `dispatch()` catching `InvalidTransitionError` from a stale-task double
  → "skipped" result, not raised further.
- `dispatch()` catching `WorkerDrainingError` → "skipped" result.
- `dispatch_all()` processes proposals in order, sequentially (assert
  call order via a recording double).
- `DispatchLoop.run_cycle()` calls `scheduler.run_cycle()` then
  `dispatcher.dispatch_all()` with its result, in that order.
- `DispatchLoop.stop()` causes `run_forever` to exit after the current
  cycle (same pattern as the existing
  `tests/scheduler/test_ordering.py::test_run_forever_stops_cleanly`).
- `DispatchLoop.run_forever` survives an exception from one cycle.

**Integration tests** (`tests/dispatch/test_dispatch_integration.py`, real
Postgres + real `WorkerRuntime`, same skip-if-unreachable pattern as
Phases 02–05):
- End-to-end: create job+task (`SUBMITTED`/`PENDING`), register a real
  `WorkerRuntime` in a `WorkerRegistry`, run `Scheduler.run_cycle()` then
  `Dispatcher.dispatch_all()` on its result → task ends `SUCCEEDED`,
  worker back to `AVAILABLE`, a `TaskAttempt` row exists, job is `QUEUED`.
- Same, with a task that fails (non-zero exit) → task `FAILED`,
  `TaskAttempt.error` set, worker still returns to `AVAILABLE`.
- Stale proposal: task already claimed by a *different* direct
  `execute_task` call before `dispatch()` runs → `dispatch()` returns
  "skipped," no exception escapes, no double-execution.
- Worker not in registry → "skipped," task remains `PENDING` untouched.
- Two proposals in one cycle, two registered workers → both eventually
  get dispatched (sequentially, §10) and both tasks succeed.
- Full `DispatchLoop.run_cycle()` end-to-end (Scheduler + Dispatcher
  together), not just `Dispatcher` in isolation.

**Regression**: full existing suite (Phases 00–05) run and must remain
green, unmodified.

Not included: heartbeat, worker-death, retry, or recovery tests — Phase
07–09.

## 19. Acceptance Criteria

- [ ] `Scheduler.run_cycle()` (Phase 05, unmodified) still produces a
      valid proposal list.
- [ ] `Dispatcher.dispatch_all()` consumes a proposal list and calls
      `dispatch()` for each pair.
- [ ] `dispatch()` never pre-validates with its own query — staleness is
      caught exclusively via `execute_task`'s existing exceptions (§9).
- [ ] Duplicate assignment is prevented — verified by an integration test
      that races two claims on the same task and asserts only one wins.
- [ ] `dispatch()` delivers the task to the correct worker via
      `WorkerRegistry` + `WorkerRuntime.execute_task()`.
- [ ] `WorkerRuntime` performs all actual execution — no execution logic
      exists in `src/atlas/dispatch/`.
- [ ] `Task`/`Worker`/`TaskAttempt` persistence is unchanged from Phase 04
      — verified by re-running Phase 04's own integration tests
      unmodified.
- [ ] Successful execution works end-to-end via `DispatchLoop`.
- [ ] Failed execution works end-to-end via `DispatchLoop`.
- [ ] Stale proposals are rejected safely (no exception escapes
      `dispatch_all`, no double-execution, no corrupted state).
- [ ] No retries, recovery, heartbeats, or gRPC code exists anywhere in
      `src/atlas/dispatch/`.
- [ ] `src/atlas/worker/runtime.py` and `src/atlas/scheduler/scheduler.py`
      are byte-for-byte unmodified.
- [ ] All existing Phase 00–05 tests still pass unmodified.

## 20. Explicit Out-of-Scope

- Scheduler redesign or priority-algorithm changes (Phase 05, untouched).
- Heartbeats (Phase 07).
- Worker failure/death detection (Phase 08).
- Retries, retry policies, task reassignment, recovery (Phase 09).
- gRPC (Phase 10).
- Message brokers (Redis/Kafka/RabbitMQ/Celery), Kubernetes.
- Distributed locks / advisory locks.
- Autoscaling.
- Observability/metrics (Phase 11).
- Authentication.
- Task sandboxing (still Phase 13, per Phase 04's plan).
- Worker capacity / multi-task-per-worker concurrency.
- Exactly-once execution semantics (never a goal, per ADR-003).
- Job-completion aggregation (`QUEUED → RUNNING/COMPLETED/FAILED`).

## 21. Files Expected to Change

New:
- `src/atlas/dispatch/__init__.py`
- `src/atlas/dispatch/registry.py` — `WorkerRegistry`.
- `src/atlas/dispatch/dispatcher.py` — `Dispatcher`
  (`dispatch`, `dispatch_all`), a small `DispatchResult` data holder.
- `src/atlas/dispatch/loop.py` — `DispatchLoop`
  (`run_cycle`, `run_forever`, `stop`).
- `tests/dispatch/test_dispatcher.py`
- `tests/dispatch/test_dispatch_integration.py`

**Not modified** (confirmed throughout this plan, not assumed):
- `src/atlas/worker/runtime.py` (Phase 04).
- `src/atlas/scheduler/scheduler.py` (Phase 05).
- `src/atlas/persistence/*` (no new repository methods needed — §12).
- `src/atlas/domain/*` (no new states/transitions — §6/§7).
- `src/atlas/api/*`, `src/atlas/services/*`, `src/atlas/main.py`.
- `docker-compose.yml` (stays Postgres-only — nothing in this phase needs
  a new service; `DispatchLoop` is exercised directly by tests, same as
  the Scheduler in Phase 05).

## 22. Dependencies

None. `threading` is stdlib and is *not* used in this phase (§10 chose
sequential dispatch) — noted so it's clear no new primitive was reached
for. If the threaded alternative in §10 is chosen instead, it would still
only need the stdlib `threading` module — either way, no new PyPI
dependency.

## 23. Risks and Design Decisions

- **Task/worker ownership**: fully delegated to Phase 04's existing,
  already-tested guards (§3) — the central design decision of this whole
  plan, and the one that keeps everything else simple.
- **Stale proposals**: handled by catching Phase 04's existing exceptions
  rather than adding a new pre-check (§9) — avoids a TOCTOU race a
  pre-check would introduce.
- **Duplicate dispatch**: same mechanism, §3/§9.
- **Execution vs. persistence ordering**: unchanged from Phase 04 (§8) —
  Phase 06 introduces no new ordering.
- **Dispatch failure**: cleanly separated from task/worker failure (§11)
  so error handling doesn't get conflated.
- **Worker draining**: `WorkerDrainingError` already exists (Phase 04) and
  is reused as-is (§9).
- **Worker crash boundary**: currently identical to "dispatcher crash"
  because both share one process (§8) — an honest limitation, not
  glossed over, and expected to change shape (not just move) once
  Phase 10 introduces a real process/network boundary.
- **Scheduler/dispatcher coupling**: deliberately kept low — `DispatchLoop`
  is the only thing that knows about both (§14).
- **Dispatcher/worker coupling**: mediated entirely by `WorkerRegistry`
  (§4/§15), the one piece of this phase explicitly designed to be thrown
  away and replaced in Phase 10.
- **Future migration to gRPC**: addressed directly in §24.
- **Open decision requiring your confirmation**: sequential vs. threaded
  `dispatch_all` (§10) — sequential is the default in this plan; flagged,
  not silently chosen, because it does trade away within-cycle worker
  parallelism.

## 24. Future gRPC Compatibility

```
Scheduler
    |  (Task, Worker) proposals — unchanged by Phase 10
    v
Dispatcher.dispatch(task, worker)      <- signature unchanged by Phase 10
    |
    v
"Worker transport"                      <- THIS is what Phase 10 replaces
    |                                      (WorkerRegistry -> gRPC stub)
    v
WorkerRuntime.execute_task(task.id)     <- unchanged; becomes the gRPC
                                            service handler's implementation
```

Phase 06's `WorkerRegistry.get(worker_id) -> WorkerRuntime` is the entire
surface Phase 10 needs to replace. When workers run as separate processes
with their own gRPC server, `Dispatcher.dispatch()`'s body changes from
"call a local object" to "call a gRPC stub for `worker.address`, which
internally calls that worker process's own `WorkerRuntime.execute_task`"
— the method signature, the caller (`DispatchLoop`), and everything
upstream (`Scheduler`) stay exactly as they are. No redesign of the
Scheduler or of Phase 04's execution model is implied by that future
change.

## 25. Implementation Sequence

1. **Registry**: `WorkerRegistry` (register/get/unregister), no
   dependencies on anything new.
2. **Dispatcher core**: `dispatch(task, worker)` — registry lookup,
   `execute_task` call, exception handling per §9/§11.
3. **Batch dispatch**: `dispatch_all(proposals)` — sequential loop (§10).
4. **Loop composition**: `DispatchLoop` wrapping `Scheduler` + `Dispatcher`
   (`run_cycle`, `run_forever`, `stop`).
5. **Unit tests**: registry, dispatcher (with doubles), loop composition
   and shutdown — no database.
6. **Integration tests**: real Postgres + real `WorkerRuntime`, full
   Scheduler→Dispatcher→Worker flow, stale-proposal and failure cases.
7. **Full suite verification**: confirm Phase 00–05 tests are unaffected;
   confirm `src/atlas/worker/runtime.py` and
   `src/atlas/scheduler/scheduler.py` are unmodified.

## 26. Definition of Done

Phase 06 is complete when every checkbox in §19 is checked, the tests in
§18 pass against the running Postgres container and skip cleanly without
it (matching the Phase 02–05 pattern), the full existing test suite
(Phases 00–05) still passes with **zero modifications to any existing
file** (only new files under `src/atlas/dispatch/` and `tests/dispatch/`),
and no scheduler redesign, gRPC, heartbeat, retry, or recovery code has
been introduced.
