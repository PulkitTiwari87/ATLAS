# Phase 05 — Scheduler

## 1. Objective

Give Atlas a Scheduler that decides **which runnable tasks should run next,
in what order, and on which eligible worker** — a pure decision component.
It reads current state (via existing repositories) and produces an ordered
proposal of `(task, worker)` pairs. It does not execute tasks (Phase 04's
`WorkerRuntime` already does that), does not deliver tasks over any
transport (Phase 06 Dispatch), does not send/receive heartbeats (Phase 07),
does not detect worker failure (Phase 08), and does not retry or recover
anything (Phase 09).

## 2. Scheduling Model

Source of truth: `docs/design/scheduling.md` (previously unread in Phase
00–04 planning; read for this phase — it resolves priority direction,
fairness, and the loop shape precisely, so nothing here is invented):

> Atlas uses a priority queue... Priority – Integer value; higher numbers
> indicate higher priority. Worker Availability – Scheduler checks the
> registry for workers in AVAILABLE state. Fairness – Simple FIFO within
> the same priority level... `task = queue.pop_highest_priority()`;
> `worker = registry.get_available_worker()`; if no worker,
> `queue.requeue(task); sleep(1)`; else `dispatcher.assign(task, worker)`.

- **Scheduler input**: `PENDING` tasks (via `TaskRepository`) and
  `AVAILABLE` workers (via `WorkerRepository`, already supports this).
- **Runnable task**: a `Task` with `status == PENDING` (see §3).
- **Task ordering**: `tasks` has no `priority` column — only
  `jobs.priority` does (`docs/database/schema.md`). A task's priority is
  its parent job's priority. Order runnable tasks by
  `(-job.priority, task.created_at)` — higher `job.priority` first, then
  oldest task first (FIFO) within equal priority, exactly matching
  scheduling.md's "higher numbers indicate higher priority" +
  "Simple FIFO within the same priority level."
- **Tie-breaking**: `task.created_at` ascending, as above. If two tasks
  share both priority and timestamp (same millisecond), order is whatever
  Python's stable sort preserves from the underlying query — not
  specifically guaranteed further; not worth solving (see §7).
- **Scheduling cycle**: one pass = compute the current runnable-task
  ordering, pair as many as possible with distinct available workers, and
  return that list of proposals. This is `Scheduler.run_cycle()`.
- **Polling vs. event-driven**: polling, matching scheduling.md's
  `while True: ... sleep(1)` shape. `Scheduler.run_forever(poll_interval)`
  loops `run_cycle()` with a sleep between cycles. No event bus, no new
  infrastructure (see §19).
- **No runnable work**: `run_cycle()` returns an empty list; the loop just
  sleeps and tries again next cycle. Not an error.

## 3. Task Eligibility

Runnable = `TaskStatus.PENDING`, full stop.

- `PENDING`: runnable.
- `ASSIGNED` / `RUNNING`: already claimed by a worker (Phase 04 owns this
  transition — see §12); excluded.
- `SUCCEEDED` / `FAILED` / `CANCELLED`: terminal; excluded.
- `RETRYING`: **excluded**. `RETRYING → PENDING` is Phase 09's transition
  to drive (`docs/design/retries.md`); Phase 05 must not treat a
  `RETRYING` task as runnable, and must not perform that transition
  itself. In practice no task can reach `RETRYING` until Phase 09 exists,
  so this is a forward-looking exclusion, not a currently-observable case.
- A `PENDING` task always has `worker_id is None` (nothing has claimed it
  yet, by construction of Phase 01/03/04) — no extra filter needed.
- **Cancelled jobs**: `JobService.cancel_job` (Phase 03) already
  transitions a job's non-terminal tasks (including `PENDING`) to
  `CANCELLED` in the same transaction as the job cancellation. By the time
  a scheduling cycle runs, a cancelled job's tasks are no longer `PENDING`
  — the `status == PENDING` filter alone correctly excludes them. No
  additional join to `jobs.status` is needed for this reason.

## 4. Worker Eligibility

Only `WorkerStatus.AVAILABLE`, using the existing
`WorkerRepository.list_by_status(AVAILABLE)` (Phase 02, already supports
this — no change needed).

- `REGISTERING`: not yet ready; excluded.
- `BUSY`: already running a task (Phase 04's binary AVAILABLE/BUSY model);
  excluded.
- `UNHEALTHY` / `DEAD` / `DRAINING`: excluded.
- **Capacity**: one task per worker, confirmed unchanged from Phase 04 —
  `WorkerStatus` has no partial-capacity state, so "how many tasks can this
  worker take" is always exactly one if `AVAILABLE`, zero otherwise. No new
  capacity field is introduced.

## 5. Task → Worker Assignment

**The Scheduler does not change any persisted `Task` or `Worker` state.**
It only reads and returns proposed pairs. Reasoning:

- `docs/design/scheduling.md`'s own pseudocode has the scheduler call
  `dispatcher.assign(task, worker)` as a *separate* step after selection —
  the docs themselves place the actual assignment in the Dispatcher, not
  the Scheduler.
- `WorkerRuntime.execute_task()` (Phase 04, `src/atlas/worker/runtime.py`)
  already performs the durable claim as its first internal step
  (`_assign`): `task.transition_to(ASSIGNED)`, set `task.worker_id`,
  `worker.transition_to(BUSY)`, all in one transaction, with the state
  machine itself preventing double-claiming (`InvalidTransitionError` if
  the task isn't `PENDING` or the worker isn't `AVAILABLE` — already
  exercised by Phase 04's `test_double_assignment_raises_invalid_transition`).
  A second claim mechanism in the Scheduler would duplicate this guarantee
  and create exactly the conflict resolved in §12/§20.
- So: **the scheduler → dispatch handoff is represented entirely by
  `Task.status == PENDING` / `Worker.status == AVAILABLE` in PostgreSQL**
  (ADR-002's single source of truth) — there is no separate "claimed by
  scheduler" flag or table. A `Scheduler.run_cycle()` proposal is an
  ephemeral, in-memory suggestion, valid only until something durably acts
  on it (in Phase 06: the Dispatcher calling `execute_task`). This keeps
  Phase 05 fully decoupled from Phase 06/04 timing — nothing breaks if a
  proposed pairing goes stale before it's acted on; the next cycle simply
  recomputes from current state.
- Transaction boundaries: `run_cycle()` performs only reads (`list_by_status`
  calls) plus, for job-status handling, the narrow write in §14. No
  transaction is held open across a "hand-off" because there isn't one.

## 6. Concurrency

- **One Scheduler process**, matching ADR-001's centralized control plane.
  Running a second instance is not a supported configuration in this
  phase; nothing here requires a distributed lock (no Redis/etcd/etc.).
- **Concurrent scheduling cycles** (e.g. a slow cycle overlapping the next
  timer tick): harmless, because cycles don't write task/worker state —
  worst case is two identical proposal lists computed close together. The
  actual claim (§5) still only ever succeeds once, downstream.
- **Scheduler vs. worker race**: a task could flip from `PENDING` to
  `ASSIGNED` (via some `execute_task` call) between the Scheduler reading
  it and returning it in a proposal. This is fine — the proposal is only a
  suggestion (§5); whatever consumes it (Phase 06) must be prepared for a
  proposal to be stale and simply skip/re-derive, not assume the pairing
  is still valid.
- **Two schedulers selecting the same task/worker**: not applicable —
  selection produces no persisted effect, so there's nothing to conflict
  over at the Scheduler layer itself.

## 7. Priority and Fairness

- Higher `jobs.priority` integer = scheduled first (§2).
- FIFO (`task.created_at` ascending) within equal priority.
- **Starvation**: explicitly not addressed, matching
  `docs/design/scheduling.md` ("Starvation – Not addressed... may be
  revisited later"). A continuous stream of high-priority tasks can starve
  low-priority ones indefinitely in this phase. Documented, not solved.
- No aging, no weighted fairness, no round-robin across jobs — none of
  that is in the docs, so none of it is built.

## 8. Scheduler Loop

- `src/atlas/scheduler/scheduler.py`, class `Scheduler`.
- `run_cycle() -> List[Tuple[Task, Worker]]`: one pass, as defined in §2/§5.
  Pure enough to unit-test without a loop or sleep.
- `run_forever(poll_interval_seconds: float = 1.0)`: loops `run_cycle()`,
  sleeping `poll_interval_seconds` between cycles — mirrors
  scheduling.md's `sleep(1)`. Matches the existing config precedent
  (Phase 00) only loosely: no new `.env` variable is required since
  scheduling.md hardcodes `sleep(1)` as the reference behavior; the
  `poll_interval_seconds` parameter defaults to `1.0` and is a constructor
  argument, not a new global setting (avoids growing `Settings` for a
  value nothing else needs yet).
- **Shutdown**: `Scheduler.stop()` sets an internal flag; `run_forever`
  checks it once per cycle and returns after the current cycle finishes
  (no mid-cycle interruption needed — a cycle is just a handful of fast
  reads, never a blocking task execution).
- **Error handling**: an exception inside one `run_cycle()` (e.g. a
  transient DB error) is caught by `run_forever`, logged via the existing
  `atlas.logging.setup_logging` logger, and the loop continues to the next
  cycle after the normal sleep — one bad cycle must not kill the scheduler
  process. `run_cycle()` itself does not swallow exceptions (so tests can
  assert on them directly); only the `run_forever` wrapper does.

## 9. Persistence Interaction

Reused as-is:
- `WorkerRepository.list_by_status(AVAILABLE)` (Phase 02).
- `JobRepository.get`, `JobRepository.update` (Phase 02/03) — for §14 only.

**One additive, minimal repository method required**, same justification
pattern as `JobRepository.list_all` (Phase 03):
- `TaskRepository.list_by_status(session, status: TaskStatus) -> List[Task]`
  — `TaskRepository` currently has `list_by_job` but no status filter,
  while `JobRepository` and `WorkerRepository` both already have
  `list_by_status`. The Scheduler needs "all `PENDING` tasks across every
  job," which no existing method provides. Same pattern, same file, one
  method — not a new abstraction.

**No new "claim" repository operation.** Per §5, the Scheduler never
claims a task, so no atomic claim/reservation method is added. This is a
deliberate simplification, not an oversight — flagged here so it's an
explicit decision, not a gap.

**Priority lookup**: no new joined-query method. The Scheduler fetches
each distinct `job_id` referenced by the runnable tasks via
`JobRepository.get` (batched by unique id, not once per task) and sorts in
Python. This keeps repositories simple (flat filtered selects only, no
cross-table joins) at the cost of a few extra `SELECT`s per cycle — an
acceptable, explicitly-not-optimized tradeoff at Phase 05's scale (Ponytail:
no premature optimization; revisit only with evidence of a real cost).

## 10. Domain State Transitions

**No changes to `TaskStatus` or `WorkerStatus` transition tables.**
The Scheduler drives zero `Task`/`Worker` transitions (§5). The only state
transition Phase 05 performs anywhere is `Job.SUBMITTED → Job.QUEUED`
(§14), which already exists in the Phase 01 `JobStatus` machine
(`src/atlas/domain/job.py`) — unused until now. No missing transition was
found; no conflict to flag here.

## 11. Dispatch Boundary

```
Scheduler  (Phase 05): reads PENDING tasks + AVAILABLE workers,
                        returns an ordered, ephemeral list of
                        (task, worker) proposals. Writes nothing
                        except the one-time Job QUEUED move (§14).

Dispatch   (Phase 06): takes a proposal and calls
                        WorkerRuntime.execute_task(task.id) on the
                        chosen worker's runtime — this is where the
                        durable PENDING -> ASSIGNED claim actually
                        happens (inside Phase 04's existing code,
                        unchanged).

Worker     (Phase 04): executes the task once claimed, exactly as
                        already implemented.
```

The persisted handoff point is `Task.status == PENDING` /
`Worker.status == AVAILABLE` — there is no Phase-05-owned durable "queue"
table or claim marker (§5). Phase 05 produces advice; Phase 06 (not built
here) is what turns advice into a real, race-safe claim by calling
already-existing Phase 04 code. No gRPC, no worker-to-Atlas transport, no
dispatch orchestration is implemented in this phase.

## 12. Worker Runtime Interaction

**Resolved conflict** (this is the one CLAUDE.md/prompt explicitly asked
to pin down): `WorkerRuntime.execute_task()` (`src/atlas/worker/runtime.py`
lines 56–72) starts from `PENDING` and performs its own
`task.transition_to(ASSIGNED)` as step one (`_assign`, lines 82–94). If
Phase 05 also performed `PENDING → ASSIGNED` itself, every subsequent
`execute_task()` call on that task would immediately raise
`InvalidTransitionError` (the task is no longer `PENDING`) — a genuine
architectural conflict.

**Resolution: Phase 05 only selects candidates; Phase 04 (via whatever
calls `execute_task`, ultimately Phase 06) owns the actual assignment.**
This is option (b), not (a). Chosen because:
1. `docs/design/scheduling.md`'s pseudocode already separates "select"
   from "assign" (dispatcher's job) — the docs agree with (b).
2. It requires zero changes to Phase 04, which is explicitly preferred
   ("Do NOT modify Phase 04 behavior unless a genuine blocking
   architectural conflict is identified" — and (b) means there is none).
3. Phase 04's existing `InvalidTransitionError` guard is already a correct,
   tested, minimal concurrency-safety mechanism for the actual claim (§5) —
   duplicating it in Phase 05 would be the "unnecessary abstraction"
   `CLAUDE.md` warns against.

**No Phase 04 code is touched by this plan.**

## 13. Failure Boundary

Phase 05 does not implement, and this plan does not design:
- Heartbeat sending/receiving.
- Worker failure/death detection.
- Retry policy or `FAILED → RETRYING` transitions.
- Task reassignment.
- Recovery of tasks left `RUNNING`/`ASSIGNED` by a dead worker.

If a worker in a scheduling proposal dies or disappears before Phase 06
acts on the proposal, or between proposal and claim, the proposal simply
goes stale (§6) — nothing in Phase 05 notices or reacts. That is Phase
07–09's responsibility, not this one's.

## 14. Job Status

Preserving the Phase 04 boundary — **task outcomes (`SUCCEEDED`/`FAILED`)
never touch `Job` status in this phase, unchanged.**

**One narrow, doc-justified exception already committed by the approved
Phase 03 plan**: `plans/phase-03-job-api.md` §"Job Creation Flow" states
verbatim: *"the job stays SUBMITTED and tasks stay PENDING. Moving a job
to QUEUED is the Scheduler's job (Phase 05) and is explicitly out of
scope [of Phase 03]."* Combined with `docs/design/job-lifecycle.md`
("QUEUED – All tasks are pending scheduling"), this means Phase 05 is
documented to perform exactly one job-status move:

> For each distinct job referenced by tasks in the current runnable set,
> if `job.status == SUBMITTED`: `job.transition_to(QUEUED)`,
> `JobRepository.update`.

This is a one-time, one-directional move per job (subsequent cycles see
`QUEUED` and skip it — `transition_to` would raise otherwise, so the
Scheduler checks `job.status == SUBMITTED` before calling it, never relies
on catching the error as control flow). **`QUEUED → RUNNING` and any
task-completion-driven aggregation remain out of scope** — no
documentation commits Phase 05 to those, and the Phase 04 boundary decision
(task outcomes don't touch Job status) still applies to them.
**Flagging this for your explicit confirmation** since it does technically
touch `Job` status, even though it's narrowly scoped and pre-committed by
the approved Phase 03 plan rather than invented here.

## 15. Testing Strategy

**Unit tests** (`tests/scheduler/test_ordering.py`, no database — construct
`Task`/`Worker`/`Job` domain objects directly and test the pure ordering
function in isolation):
- higher job-priority task selected before lower.
- equal priority: earlier `created_at` selected first (FIFO).
- `RETRYING`/`ASSIGNED`/`RUNNING`/terminal-status tasks excluded from
  runnable set.
- `BUSY`/`REGISTERING`/`DRAINING`/`DEAD`/`UNHEALTHY` workers excluded from
  eligible set.
- empty runnable set or empty eligible-worker set → empty proposal list.
- more runnable tasks than available workers → only as many proposals as
  workers, in priority order (highest-priority tasks proposed first).
- `Scheduler.stop()` causes `run_forever` to exit after the current cycle
  (use a short poll interval and a cycle counter/mock).

**Integration tests** (`tests/scheduler/test_scheduler_integration.py`,
real Postgres, same skip-if-unreachable pattern as Phases 02–04):
- a `PENDING` task with a `SUBMITTED` job + an `AVAILABLE` worker produces
  a proposal, and the job is moved to `QUEUED`.
- a job already `QUEUED` is not re-transitioned (no error, no-op).
- a `CANCELLED` job's tasks (via `JobService.cancel_job`) never appear in
  a proposal.
- no `AVAILABLE` workers → empty proposals, task stays `PENDING`.
- `run_cycle()` does not itself change any `Task` or `Worker` row — assert
  DB state is identical to a `WorkerRepository`/`TaskRepository` read
  taken immediately after the cycle (proves §5's "reads only" claim, not
  just the docstring).
- calling `WorkerRuntime.execute_task()` after a task appears in a
  Scheduler proposal still succeeds exactly as it does today (regression
  guard for §12's resolution — proves Phase 04 needed no changes).

**Regression**: full existing suite (Phases 00–04) run and must remain
green, unmodified.

Not included: any test involving heartbeats, worker death, retries, or
recovery — those belong to Phase 07–09.

## 16. Acceptance Criteria

- [ ] `Scheduler.run_cycle()` returns runnable tasks ordered by
      `(-job.priority, task.created_at)`.
- [ ] Only `PENDING` tasks are considered runnable; `RETRYING` and all
      other statuses are excluded.
- [ ] Only `AVAILABLE` workers are considered eligible.
- [ ] A cycle proposes at most `min(len(runnable), len(available))` pairs,
      never assigning one worker to two tasks or vice versa within a
      single cycle's output.
- [ ] `run_cycle()` never changes `Task.status`, `Task.worker_id`, or
      `Worker.status` — verified directly against the database in an
      integration test, not just by code inspection.
- [ ] A `SUBMITTED` job with a runnable task is moved to `QUEUED`;
      an already-`QUEUED` job is left alone (no exception, no change).
- [ ] `WorkerRuntime.execute_task()` (Phase 04) continues to work
      unmodified and unaffected by the Scheduler having run.
- [ ] No worker failure detection, retry, recovery, dispatch transport, or
      gRPC code is introduced.
- [ ] `run_forever()` can be started and cleanly stopped via `stop()`.
- [ ] All existing Phase 00–04 tests still pass unmodified.

## 17. Explicit Out-of-Scope

- Worker execution (Phase 04, unchanged, reused as-is).
- Dispatch transport / orchestration (Phase 06).
- gRPC (Phase 10).
- Heartbeats (Phase 07).
- Failure detection (Phase 08).
- Retries, `RETRYING` handling, task reassignment, recovery (Phase 09).
- Job status aggregation beyond the single documented `SUBMITTED → QUEUED`
  move (§14) — no `QUEUED → RUNNING`, no task-outcome-driven aggregation.
- Observability/metrics (Phase 11).
- Authentication (not in the current API design).
- Autoscaling.
- Distributed message brokers (Redis/Kafka/RabbitMQ/Celery) or
  Kubernetes.
- Advanced fairness/starvation-prevention algorithms (§7).
- Any new `.env`/`Settings` field (poll interval is a constructor default,
  §8).

## 18. Files Expected to Change

New:
- `src/atlas/scheduler/__init__.py`
- `src/atlas/scheduler/scheduler.py` — `Scheduler` (`run_cycle`,
  `run_forever`, `stop`), ordering/eligibility logic.
- `tests/scheduler/test_ordering.py`
- `tests/scheduler/test_scheduler_integration.py`

Modified (additive only):
- `src/atlas/persistence/repositories.py` — add
  `TaskRepository.list_by_status` (§9). No other change.

Not touched: `src/atlas/domain/*`, `src/atlas/persistence/models.py`,
`src/atlas/persistence/db.py`, `JobRepository`/`WorkerRepository`
internals, `src/atlas/worker/*`, `src/atlas/api/*`, `src/atlas/services/*`,
`src/atlas/main.py`, `docker-compose.yml`.

## 19. Dependencies

None. Only `time.sleep` (stdlib) for `run_forever`'s poll interval, plus
the already-present `SQLAlchemy`/repositories. No Redis, Kafka, RabbitMQ,
Celery, or Kubernetes.

## 20. Risks and Design Decisions

- **Duplicate task assignment**: prevented entirely by Phase 04's existing
  `InvalidTransitionError` guard (§5/§12) — Phase 05 introduces no new
  claim mechanism to get this wrong.
- **Concurrent scheduler cycles**: harmless by construction — no writes to
  race over except the idempotent-in-effect `Job` QUEUED move (§14),
  which two overlapping cycles might both attempt; both writes are
  identical (`status = QUEUED`) so a "last write wins" race is harmless,
  not a correctness bug. No optimistic locking added (consistent with
  Phase 02's deferred decision).
- **Task ownership / worker ownership**: entirely owned by the
  `PENDING`/`ASSIGNED` and `AVAILABLE`/`BUSY` state machines, unchanged
  from Phase 01/04. Phase 05 never takes ownership of anything.
- **Atomic state transitions**: not needed at the Scheduler layer, since
  it performs none of the transitions that matter for correctness (only
  the harmless idempotent `QUEUED` move).
- **Stale worker state**: a proposal can reference a worker that goes
  `BUSY`/`DEAD` before Phase 06 acts on it. Explicitly acceptable — see
  §6/§13; Phase 06's job (not designed here) is to handle a stale/rejected
  proposal by re-deriving from current state, not to trust the proposal
  blindly.
- **Scheduler crash**: no persisted scheduler-side state exists to
  corrupt; restarting `run_forever` just resumes reading current DB state
  from scratch. No recovery logic needed.
- **Scheduler/dispatch boundary**: resolved explicitly in §11/§12 — this
  is the plan's central design decision and the one most likely to need
  revisiting once Phase 06 is actually designed; flagged for your
  awareness, not left implicit.
- **Interaction with Phase 04**: resolved in §12 — zero Phase 04 changes
  required or made.

## 21. Implementation Sequence

1. **Repository**: add `TaskRepository.list_by_status` (§9).
2. **Scheduler decision logic**: pure ordering/eligibility function(s) in
   `Scheduler` — no I/O beyond calling the two `list_by_status` methods
   and (for priority) `JobRepository.get`.
3. **Job QUEUED move**: the narrow §14 write, as its own small step inside
   `run_cycle()`.
4. **Scheduler loop**: `run_forever` + `stop()` wrapping `run_cycle`.
5. **Unit tests**: ordering/eligibility (§15), no database.
6. **Integration tests**: against the Phase 00 Postgres container (§15).
7. **Full suite verification**: confirm Phase 00–04 tests are unaffected,
   confirm `WorkerRuntime.execute_task()` still works after a Scheduler
   cycle has run (regression guard for §12).

## 22. Definition of Done

Phase 05 is complete when every checkbox in §16 is checked, the tests in
§15 pass against the running Postgres container and skip cleanly without
it (matching the Phase 02–04 pattern), the full existing test suite
(Phases 00–04) still passes with zero modifications to those files beyond
the one additive repository method in §18, and no dispatch, gRPC,
heartbeat, retry, or recovery code has been introduced.
