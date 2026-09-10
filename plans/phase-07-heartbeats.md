# Phase 07 — Heartbeats

## 1. Objective

Give each `WorkerRuntime` a way to periodically prove **it is alive** by
updating `Worker.last_heartbeat` in PostgreSQL. Heartbeat means liveness
only — it says nothing about task success, worker health classification,
or whether reassignment is warranted. Those are Phase 08/09.

## 2. Scope

- `WorkerRuntime` gains a background heartbeat sender (additive — see §12
  for the exact, minimal touch to Phase 04's file).
- One new, narrow repository write path to avoid a real race with
  `execute_task()`'s existing full-row `WorkerRepository.update()` (§9).
- No detection, no state reinterpretation, no reassignment.

## 3. Responsibilities

- **Worker (sender)**: periodically writes its own `last_heartbeat`.
- **PostgreSQL (recorder)**: durable store of `workers.last_heartbeat`,
  unchanged column, already exists (Phase 02 schema, `docs/database/
  schema.md`).
- Nothing in this phase reads or interprets heartbeats — that consumer is
  Phase 08's `FailureDetector`, not built here.

## 4. Architecture

- **Heartbeat source**: `WorkerRuntime` itself — the same process/object
  that owns `self.worker`, so no separate "heartbeat agent" component.
- **Mechanism**: a background daemon thread
  (`threading.Thread(target=self._heartbeat_loop, daemon=True)`),
  started by `start_heartbeat()`, stopped by `stop_heartbeat()`.
  **Why a thread is required, not optional**: `execute_task()` is
  synchronous and blocks for the full duration of task execution (Phase
  04, unchanged). If heartbeats were sent only between `execute_task`
  calls, a worker executing one long task would stop appearing alive
  exactly while it's doing legitimate work — the opposite of what a
  liveness signal should show. A background thread is the smallest
  change that keeps heartbeats flowing during `BUSY` execution without
  touching `execute_task`'s own synchronous design.
- **Worker identity**: `self.worker.id` (Phase 04, unchanged).
- **Heartbeat payload**: nothing over the wire in this phase (still
  in-process, per the Phase 06 temporary-transport precedent) — just
  `worker_id` implicitly (via the repository call) plus a timestamp.
  `docs/api/grpc-api.md`'s `HeartbeatRequest`/`HeartbeatResponse` are
  Phase 10 concerns; not built here.
- **Interval**: `settings.heartbeat_interval` (already exists, Phase 00,
  default `5`, matches `docs/design/heartbeats.md`). No new config field.

## 5. Inputs

- `self.worker.id` (identity).
- Wall-clock time (`datetime.now(timezone.utc)`).
- `settings.heartbeat_interval` (existing config).

## 6. Outputs

- `workers.last_heartbeat` updated in PostgreSQL, once per interval, for
  as long as the worker is running (from `register()` through the start
  of `shutdown()`).

## 7. State Transitions

**None.** Sending a heartbeat never calls `worker.transition_to(...)`.
`WorkerStatus` is completely unaffected by this phase — a heartbeat is a
timestamp write, not a status change. No modification to
`src/atlas/domain/worker.py`.

## 8. Persistence Interaction

- New, narrow method: `WorkerRepository.update_heartbeat(session,
  worker_id, timestamp)` — see §9 for why this must be narrower than the
  existing `WorkerRepository.update()`.
- No new table, no new column — `workers.last_heartbeat` already exists
  (nullable `DateTime(timezone=True)`, Phase 02).

## 9. Transaction Boundaries

Each heartbeat tick is its own short transaction: open, run the targeted
`UPDATE workers SET last_heartbeat = :ts WHERE id = :id`, commit, close.
Never held open across the sleep between ticks, never correlated with
`execute_task()`'s transactions.

**Why a *new*, narrower repository method is required (this is the one
genuine blocking issue this phase identifies):** the existing
`WorkerRepository.update()` writes *every* column
(`hostname`, `address`, `status`, `last_heartbeat`) from whatever
`Worker` object is passed in. The heartbeat thread and the main thread
(inside `execute_task`'s `_assign`/`_finish`) both call `.update()` on the
*same* `WorkerRepository` against the *same* `self.worker` object,
concurrently. If the heartbeat thread's tick reads a slightly stale
in-memory `self.worker.status` (e.g. it captured `AVAILABLE` a moment
before the main thread committed `AVAILABLE → BUSY`), the heartbeat's
full-row update could silently overwrite `status` back to a stale value —
a real lost-update race on a field the heartbeat thread has no business
touching at all. `update_heartbeat()` writes `last_heartbeat` only,
eliminating the race entirely rather than trying to synchronize around it.

## 10. Concurrency Model

- One background thread per `WorkerRuntime` instance, started once at
  `register()`, stopped once at `shutdown()`.
- The heartbeat thread and the main thread never write the same column
  concurrently (§9) — no lock is needed between them.
- `self.worker` (the Python object) is read by the heartbeat thread
  (`self.worker.id`) and mutated by the main thread (`.status`, via
  `transition_to`). Reading `.id` (immutable after construction) is race-
  free by construction; CPython's GIL makes individual attribute access
  atomic enough that no torn reads occur, and since the heartbeat thread
  never reads `.status`, there's nothing further to protect.
- No new locking primitive beyond `threading.Thread` itself.

## 11. Failure Behavior

- Heartbeat write fails (e.g. transient DB error): caught inside the loop
  iteration, logged via `atlas.logging`, loop continues to the next tick
  — one failed heartbeat must not kill the thread (same "don't let one
  bad cycle end the loop" pattern as `Scheduler.run_forever`).
- **Missed heartbeats mean nothing in this phase.** No timeout logic, no
  status change, no error raised to callers of `execute_task()`. Absence
  of a recent heartbeat is Phase 08's signal to interpret, not this
  phase's.

## 12. Interaction with Previous Phases

Additive touch-points to `src/atlas/worker/runtime.py` (Phase 04) —
**flagging exactly which methods change, per CLAUDE.md's "don't silently
modify" rule**:
- `register()`: after the existing `AVAILABLE` transition, call
  `self.start_heartbeat()`. Everything `register()` already does is
  unchanged; one call is appended.
- `shutdown()`: call `self.stop_heartbeat()` *before* the existing
  `DRAINING → DEAD` transitions, so a `DEAD` worker never reports itself
  alive afterward. Everything `shutdown()` already does is unchanged;
  one call is prepended.
- `execute_task()`: **no change.** Heartbeats run independently of task
  execution (§4) — this is exactly why they need their own thread instead
  of being woven into the execution method.

No change to `src/atlas/scheduler/scheduler.py` — Scheduler's eligibility
check (`WorkerStatus.AVAILABLE`) is unaffected; it doesn't look at
`last_heartbeat` in this phase (Phase 08 will).

## 13. Interaction with Next Phases

Phase 08's `FailureDetector` reads `worker.last_heartbeat` (this phase's
sole output) to decide staleness. Phase 09's recovery never touches
heartbeats directly. Phase 10 replaces the *sending mechanism*'s transport
(a local repository call becomes a `Heartbeat` gRPC call) but not its
shape — `send_heartbeat()`'s contract (worker proves liveness, on an
interval) is unchanged by that future swap.

## 14. API/Service Implications

None. No REST/gRPC endpoint changes in this phase — heartbeats stay
entirely inside the worker process talking directly to PostgreSQL, same
temporary-transport precedent as Phase 06.

## 15. Files Expected to Change

Modified (additive only):
- `src/atlas/worker/runtime.py` — `start_heartbeat`, `stop_heartbeat`,
  `_heartbeat_loop`, `send_heartbeat`; two one-line call insertions in
  `register()`/`shutdown()` (§12).
- `src/atlas/persistence/repositories.py` — add
  `WorkerRepository.update_heartbeat` (§9).

New:
- `tests/worker/test_heartbeat.py` — unit tests.
- Extend `tests/worker/test_task_execution.py` or add
  `tests/worker/test_heartbeat_integration.py` — integration tests.

Not touched: `src/atlas/domain/*`, `src/atlas/scheduler/*`,
`src/atlas/dispatch/*`, `src/atlas/api/*`, `src/atlas/services/*`,
`docker-compose.yml`.

## 16. Dependencies

None. `threading` (stdlib) only.

## 17. Testing Strategy

**Unit** (`tests/worker/test_heartbeat.py`, mock the repository call to
avoid needing a thread+DB round trip for timing-sensitive assertions):
- `send_heartbeat()` calls `WorkerRepository.update_heartbeat` with the
  worker's id and a current timestamp.
- `start_heartbeat()` then `stop_heartbeat()` cleanly starts and joins the
  background thread (assert the thread is not alive after stop).
- `stop_heartbeat()` before `start_heartbeat()` is a safe no-op.

**Integration** (real Postgres, same skip pattern as Phases 02–06):
- `register()` starts heartbeating; after `~2x` the interval,
  `workers.last_heartbeat` has advanced (poll and compare timestamps).
- `shutdown()` stops heartbeating; `last_heartbeat` does not advance
  further after `DEAD`.
- Heartbeats continue to update during a synchronous `execute_task()`
  call (start a task with `payload={"command": "sleep 1"}` or similar,
  assert at least one heartbeat lands during that window) — the
  regression test that actually proves §4's justification, not just
  asserts it.
- Concurrent heartbeat + `execute_task()` does not corrupt
  `worker.status` (regression test for §9 — run both, assert final
  status is exactly what `execute_task`'s own transitions produced, no
  reversion).

**Regression**: full existing suite (Phases 00–06) passes unmodified.

Not included: any test asserting a worker is marked dead/unhealthy from a
missing heartbeat — that belongs to Phase 08.

## 18. Acceptance Criteria

- [ ] A registered worker's `last_heartbeat` advances roughly every
      `settings.heartbeat_interval` seconds.
- [ ] Heartbeats continue while the worker is `BUSY` (mid-`execute_task`).
- [ ] `shutdown()` stops heartbeats before the worker reaches `DEAD`.
- [ ] No `WorkerStatus` transition is ever triggered by a heartbeat.
- [ ] Concurrent heartbeat writes never revert a concurrently-committed
      `status` change (verified against the DB, not just code inspection).
- [ ] All existing Phase 00–06 tests still pass unmodified.

## 19. Explicit Out-of-Scope

- Declaring a worker dead or unhealthy (Phase 08).
- Task reassignment, retries, recovery (Phase 09).
- Modifying `Task.attempt`/`max_retries` (never this phase's concern).
- gRPC `Heartbeat` RPC (Phase 10) — still a direct repository call.
- Any new `WorkerStatus` value.

## 20. Risks and Design Decisions

- **Thread-per-worker overhead**: negligible at Atlas's expected scale
  (one OS thread per worker process, not per task) — not optimized
  further without evidence.
- **Clock skew** between worker and DB server: not addressed; Phase 08's
  threshold comparison will inherit whatever skew exists. Acceptable —
  not a new risk introduced by this phase, and not solvable generically
  without NTP-level guarantees out of scope for Atlas.
- **The `update_heartbeat` narrow-write fix (§9) is the one required,
  non-optional persistence change** in this phase — without it, Phase 07
  would introduce a real correctness regression into Phase 04's worker
  state handling, not just a theoretical one.

## 21. Implementation Sequence

1. `WorkerRepository.update_heartbeat` (persistence, §9).
2. `WorkerRuntime.send_heartbeat()` (single-tick logic).
3. `WorkerRuntime._heartbeat_loop`, `start_heartbeat`, `stop_heartbeat`.
4. Wire into `register()`/`shutdown()` (§12, two one-line insertions).
5. Unit tests.
6. Integration tests, including the concurrency regression test (§17).
7. Full suite verification.

## 22. Definition of Done

Phase 07 is complete when every checkbox in §18 passes against the live
Postgres container and the tests skip cleanly without it, the full
existing suite (Phases 00–06) still passes, and the only modified files
are the two listed in §15 (both additive, no removed/redesigned
behavior).
