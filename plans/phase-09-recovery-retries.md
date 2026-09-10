# Phase 09 — Recovery + Retries

## 1. Objective

Given a worker Phase 08 has marked `UNHEALTHY`, recover the tasks it left
orphaned (`RUNNING`/`ASSIGNED`, still pointing at it via `Task.worker_id`)
and apply Atlas's retry policy, then retire the worker to `DEAD`. This is
the phase where **at-least-once execution stops being a documented
promise and becomes an observable behavior** — see §8.

## 2. Scope

- `RecoveryManager`: polling loop, same shape as `Scheduler`/
  `DispatchLoop`/`FailureDetector`.
- One new repository method (`TaskRepository.list_by_worker`).
- **One flagged, not-yet-made domain change** — see §6, this is the plan's
  central finding.
- Retry-vs-terminal decision using existing `Task.attempt`/
  `Task.max_retries` fields (Phase 01/02, unused for policy until now).
- `UNHEALTHY → DEAD` worker transition (existing edge, first real use).

## 3. Responsibilities

- **Phase 07**: records liveness.
- **Phase 08**: interprets missing liveness → `UNHEALTHY`.
- **Phase 09** (this plan): recovers orphaned tasks, applies retry policy,
  retires the worker.

## 4. Architecture

`src/atlas/recovery/recovery.py`, `RecoveryManager`:
- `run_cycle()`: load `UNHEALTHY` workers (`WorkerRepository.
  list_by_status`, reused, no new method). For each: recover its
  orphaned tasks (§5–§7), then transition the worker `UNHEALTHY → DEAD`.
- `run_forever(poll_interval_seconds)` / `stop()`: same established
  pattern.

## 5. Inputs

- `Worker` rows with `status == UNHEALTHY` (Phase 08's output).
- `Task` rows with `worker_id == <that worker>` and
  `status in {RUNNING, ASSIGNED}` — the orphaned set.

## 6. Task State Machine — the required flag

Verified against `src/atlas/domain/task.py`'s `_ALLOWED_TRANSITIONS`:

```
PENDING:   {ASSIGNED, CANCELLED}
ASSIGNED:  {RUNNING, CANCELLED}          <-- no ASSIGNED -> FAILED edge
RUNNING:   {SUCCEEDED, FAILED, CANCELLED}
FAILED:    {RETRYING}
RETRYING:  {PENDING}
```

Recovery needs to move an orphaned task toward `FAILED` (so the existing
`FAILED → RETRYING → PENDING` chain can apply retry policy uniformly). For
a `RUNNING` orphan this is already legal (`RUNNING → FAILED` exists — no
change needed). **For an `ASSIGNED` orphan (a task claimed by a worker
that died before it ever reached `RUNNING` — e.g. the dispatcher/worker
process died between `_assign()` and `_start()`, Phase 04 §4 steps 2–3) —
there is no `ASSIGNED → FAILED` edge.** The only existing option from
`ASSIGNED` besides `RUNNING` is `CANCELLED`, which is semantically wrong
(this isn't a user cancellation) and would also permanently block retries
(`CANCELLED` is terminal — no outgoing edges at all).

**This is a genuine blocking inconsistency, flagged per `CLAUDE.md` §8
rather than silently fixed.** Recommended resolution: add
`TaskStatus.ASSIGNED: {TaskStatus.RUNNING, TaskStatus.CANCELLED,
TaskStatus.FAILED}` to `src/atlas/domain/task.py` — one new edge,
justified by a real scenario the existing state machine (designed in
Phase 01, before any worker-failure handling existed) never had to
account for. **This is the one domain change this plan proposes anywhere
in Phases 07–09, and it requires your explicit confirmation before
Phase 09 implementation begins** — it is not made by this planning pass.

With that edge added, both orphan cases converge on the same handling:

```
RUNNING/ASSIGNED --(RecoveryManager)--> FAILED
FAILED           --(RecoveryManager, if attempt < max_retries)--> RETRYING --> PENDING
FAILED           --(terminal, if attempt >= max_retries)--> (stays FAILED)
```

## 7. Retry Policy

Per `docs/design/retries.md` (unchanged, no new concepts invented):
- `max_retries` — already on `Task`, default `3` (Phase 01/02), matches
  `settings.max_retries`'s default.
- **No error classification** — every failure (executor error, non-zero
  exit, *or* worker death) counts the same toward the retry limit, exactly
  as `retries.md` specifies ("Currently all failures trigger a retry
  until the limit is reached").
- **No backoff** — `FAILED → RETRYING → PENDING` happens immediately, in
  the same recovery pass, matching `retries.md`'s "tasks are re-queued
  immediately."
- **Who increments `Task.attempt`?** `WorkerRuntime._start()` does,
  unchanged, exactly once per actual `RUNNING` entry (Phase 04). Recovery
  **only reads** `task.attempt` to decide retry-vs-terminal — it never
  increments it itself. This is worth stating explicitly since it's easy
  to double-count otherwise.
- **`Task.worker_id` is cleared** (`None`) when a task is recovered to
  `PENDING` — it no longer belongs to any worker. Left as-is (historical)
  if the task reaches terminal `FAILED` instead — not required for
  correctness, just a data-hygiene choice, cheapest to leave alone
  (smallest diff).
- **Retry exhaustion**: `attempt >= max_retries` → task stays `FAILED`,
  no further transition attempted. Matches `retries.md` exactly ("When
  retry_count exceeds max_retries, the task transitions to FAILED" — it's
  already there; recovery just declines to move it further).

## 8. At-Least-Once Semantics — explicit duplicate-execution analysis

This is the risk `CLAUDE.md` §10 and ADR-003 name directly, and this
phase is where it becomes concrete:

```
Worker starts executing Task X (RUNNING)
    -> Worker crashes before WorkerRuntime._finish() persists a result
    -> Phase 07 stops seeing heartbeats from that worker
    -> Phase 08 marks the worker UNHEALTHY (after settings.worker_timeout)
    -> Phase 09 (this phase) finds Task X still RUNNING, owned by a
       now-UNHEALTHY worker
    -> Atlas has NO way to know whether the worker's copy of Task X
       actually finished (successfully or not) before it crashed —
       no result was ever persisted, because _finish() never ran.
    -> Recovery moves Task X: RUNNING -> FAILED -> RETRYING -> PENDING
       (if attempt < max_retries)
    -> A different (or the same, restarted) worker will eventually
       execute Task X again via the normal Scheduler -> Dispatch ->
       WorkerRuntime path (Phases 05/06, unchanged) -> duplicate
       execution, if the original silently succeeded.
```

**This is accepted, not solved.** Per `docs/design/idempotency.md`, task
authors are responsible for idempotent task logic; Atlas provides no
deduplication or idempotency-key mechanism (documented there as future
work, still out of scope in Phase 09). Recovery's job is only to make
forward progress possible — it cannot and does not try to determine what
actually happened on the dead worker.

**Recovery after scheduler/dispatcher failure**: if the control-plane
process itself crashes mid-`RecoveryManager` cycle, nothing is lost beyond
whatever single task's recovery transaction was in flight (§10) — the
next cycle re-queries `UNHEALTHY` workers and their orphaned tasks fresh
from the DB and picks up exactly where it left off. No persisted
"recovery progress" state is needed, matching the same crash-resilience
argument already established for `Scheduler` (Phase 05 §20).

## 9. Worker State Transition

```
UNHEALTHY --(RecoveryManager, after all its orphaned tasks are handled)--> DEAD
```

Already a valid edge (Phase 01, unused until now) — no domain change.
Performed only after every one of that worker's `RUNNING`/`ASSIGNED`
tasks has been resolved (moved to `FAILED`/`PENDING`) — a worker is never
marked `DEAD` while it still "owns" an unresolved task, so nothing else
in the system can be confused about who's responsible for what.

## 10. Persistence Interaction / Transaction Boundaries

- **New**: `TaskRepository.list_by_worker(session, worker_id) ->
  List[Task]` — mirrors the existing `list_by_job` pattern exactly.
  Needed because no existing method can answer "which tasks does this
  worker currently own," and that's exactly what recovery needs first.
- Reused as-is: `WorkerRepository.list_by_status`, `.get`, `.update`
  (Phase 02/04); `TaskRepository.get`, `.update` (Phase 02).
- **One short transaction per task** (fetch fresh, decide, transition(s),
  update, commit) — not one giant transaction for a worker's whole task
  set, matching the established per-entity-transaction pattern (Scheduler
  §9, FailureDetector §10). The `FAILED → RETRYING → PENDING` chain for a
  single task happens inside *one* transaction (both `transition_to`
  calls, one `update`, one commit) since it's one logical recovery
  decision for that task.
- **One more short transaction** per worker, after all its tasks are
  handled, for the `UNHEALTHY → DEAD` move.
- No transaction is ever held open across multiple tasks or across the
  loop's sleep — same discipline as every prior phase.

## 11. Concurrency Model

- **Recovery vs. a late-arriving result**: not possible in the current
  synchronous, in-process `execute_task()` model — if the original
  worker's process is still alive enough to call `_finish()`, it isn't
  actually dead, and Phase 08 wouldn't have marked it `UNHEALTHY` (subject
  to the timing/threshold caveats already noted in Phase 08 §20). Once
  Phase 10 makes execution asynchronous over gRPC, "late result after
  recovery already reassigned the task" becomes a real race requiring
  its own handling — **explicitly not solved here**, flagged as a Phase
  10-adjacent risk to revisit, not invented now.
- **Recovery vs. Scheduler/Dispatch**: once a recovered task reaches
  `PENDING` with `worker_id = None`, it re-enters the normal
  Scheduler/Dispatch flow (Phases 05/06, completely unchanged) — no
  special-casing needed; a recovered task looks exactly like any other
  `PENDING` task to those components.
- **Two `RecoveryManager` cycles overlapping**: harmless — the second
  cycle re-fetches fresh state; a task already moved to `PENDING` by the
  first cycle no longer matches the `RUNNING`/`ASSIGNED` + `worker_id`
  filter, so it's simply not reprocessed. A worker already `DEAD` is no
  longer `UNHEALTHY`, so it's not reprocessed either.
- **No new locking.** Per-task and per-worker short transactions plus
  the existing state-machine guards are sufficient, matching the same
  "prefer PostgreSQL transactional guarantees" instruction Phase 08
  followed.

## 12. Interaction with Previous Phases

- Consumes Phase 08's `UNHEALTHY` workers directly.
- Reuses Phase 04's `TaskStatus`/`WorkerStatus` machinery exclusively (no
  parallel transition logic invented).
- **Does not modify** `src/atlas/worker/runtime.py`,
  `src/atlas/scheduler/scheduler.py`, `src/atlas/dispatch/*`,
  `src/atlas/failure_detection/*` (Phase 08, planned but not yet built).
- The one domain change (§6) touches only `src/atlas/domain/task.py`'s
  transition table — not `Job`, not `Worker`, not any other Task field or
  method.

## 13. Interaction with Next Phases

Recovered tasks flow back through Phases 05/06 exactly like any other
`PENDING` task — no new integration surface for those phases to build.
Phase 10 (gRPC) will need to reconsider §11's "late result" caveat once
execution is asynchronous, but that's Phase 10's problem to pick up, not
this plan's to pre-solve.

## 14. API/Service Implications

None. No REST/gRPC surface changes. `JobService`/`JobRepository` are not
touched — per the established boundary (Phase 04/05), task outcomes
(including recovery-driven ones) still never touch `Job` status.

## 15. Files Expected to Change

New:
- `src/atlas/recovery/__init__.py`
- `src/atlas/recovery/recovery.py` — `RecoveryManager`.
- `tests/recovery/test_recovery.py` (unit)
- `tests/recovery/test_recovery_integration.py` (integration)

Modified:
- `src/atlas/persistence/repositories.py` — add
  `TaskRepository.list_by_worker` (§10).
- `src/atlas/domain/task.py` — **pending your confirmation** — add the
  `ASSIGNED → FAILED` edge (§6). If not confirmed, Phase 09 cannot
  correctly recover `ASSIGNED`-orphan tasks and this plan's design must
  be revisited before implementation.

Not touched: `Job`/`Worker` domain files, `src/atlas/worker/runtime.py`,
`src/atlas/scheduler/scheduler.py`, `src/atlas/dispatch/*`,
`src/atlas/api/*`, `src/atlas/services/*`, `docker-compose.yml`.

## 16. Dependencies

None. Stdlib + existing SQLAlchemy/repositories only.

## 17. Testing Strategy

**Unit** (`tests/recovery/test_recovery.py`, no database — construct
`Task`/`Worker` objects directly):
- `attempt < max_retries` → task ends `PENDING`, `worker_id` cleared.
- `attempt >= max_retries` → task ends `FAILED` (terminal), no further
  transition attempted.
- both `RUNNING` and `ASSIGNED` orphans are handled identically once §6's
  edge exists.
- a worker with zero orphaned tasks still transitions `UNHEALTHY → DEAD`.
- `run_forever`/`stop` shutdown behavior (established pattern).

**Integration** (real Postgres, same skip pattern):
- full flow: create job+task, manually drive it to `RUNNING` with
  `worker_id` set to a worker manually set `UNHEALTHY` → run
  `RecoveryManager.run_cycle()` → task is `PENDING`, `worker_id is None`,
  `attempt` unchanged from before recovery (only `_start()` increments
  it) → worker is `DEAD`.
- same, with `task.attempt` pre-set to `task.max_retries` → task ends
  `FAILED`, stays `FAILED`, worker still reaches `DEAD`.
- `ASSIGNED`-orphan case (task never reached `RUNNING`) recovers
  correctly (depends on §6's edge).
- a recovered `PENDING` task is picked up correctly by a subsequent
  `Scheduler.run_cycle()` + `Dispatcher.dispatch_all()` — an explicit
  regression proving Phases 05/06 need no changes to consume Phase 09's
  output.
- two overlapping `run_cycle()` calls don't double-recover the same task
  or double-transition the same worker.

**Regression**: full existing suite (Phases 00–08) passes unmodified.
Verified by inspection: `tests/domain/test_task.py`'s
`test_invalid_transitions_are_rejected` parametrization currently asserts
only `(TaskStatus.ASSIGNED, TaskStatus.SUCCEEDED)` as an invalid
transition from `ASSIGNED` — it does not assert `ASSIGNED → FAILED` is
invalid. So adding that edge (§6) requires no change to any existing
test assertion; a new case should be added to
`tests/domain/test_task.py` confirming `ASSIGNED → FAILED` is now valid
and `ASSIGNED → SUCCEEDED` remains invalid.

Not included: any gRPC, observability, or hardening-specific test —
those belong to Phases 10–13.

## 18. Acceptance Criteria

- [ ] Every `RUNNING`/`ASSIGNED` task owned by an `UNHEALTHY` worker is
      recovered (moved to `PENDING` or terminal `FAILED`, never left
      orphaned).
- [ ] Retry policy respects `attempt` vs. `max_retries` exactly as
      documented.
- [ ] `Task.attempt` is never incremented by `RecoveryManager` (only read).
- [ ] Recovered tasks have `worker_id` cleared.
- [ ] A worker reaches `DEAD` only after all its orphaned tasks are
      resolved.
- [ ] A recovered `PENDING` task is correctly re-scheduled/re-dispatched
      by the unmodified Phase 05/06 components.
- [ ] No duplicate execution *prevention* is attempted or claimed —
      duplicate execution remains possible and is documented, not solved.
- [ ] All existing Phase 00–08 tests still pass, and the one intentional
      domain-model addition (§6) is called out explicitly, not silent.

## 19. Explicit Out-of-Scope

- Anything in Phase 07/08 (heartbeat sending, failure detection) —
  consumed, not reimplemented.
- gRPC (Phase 10).
- Late-result-after-recovery handling under an asynchronous transport
  (deferred to whenever Phase 10 makes it a real concern, §11).
- Exactly-once semantics — never a goal (ADR-003).
- Job status aggregation — task outcomes, recovery-driven or not, still
  never touch `Job` status (§14).
- Backoff/error classification for retries (`retries.md` explicitly
  defers this too).

## 20. Risks and Design Decisions

- **The `ASSIGNED → FAILED` domain edge (§6) is this plan's central,
  required decision** — flagged clearly, not silently added. Without it,
  Phase 09 has no correct way to recover a task that was claimed but
  never started.
- **Duplicate execution is real and accepted** (§8) — the plan documents
  it precisely rather than glossing over it.
- **False-positive `UNHEALTHY` recovery** (a worker Phase 08 marked
  `UNHEALTHY` under clock/scheduling jitter, not actually dead): Phase 09
  will recover its tasks regardless — the current architecture has no
  mechanism for a worker to contest or reverse an `UNHEALTHY` marking
  (Phase 08 §20's noted one-way-door). Accepted as a known, documented
  limitation of the current design, not fixed here.
- **Recovery is single-instance** — same ADR-001 centralized-control-
  plane assumption as Scheduler/FailureDetector; no distributed
  coordination needed or built.

## 21. Implementation Sequence

1. **Domain** (pending confirmation, §6): add
   `ASSIGNED → FAILED` to `src/atlas/domain/task.py`.
2. **Repository**: `TaskRepository.list_by_worker`.
3. **Recovery decision logic**: retry-vs-terminal, pure function, tested
   without a database first.
4. **Per-task recovery**: the transaction in §10.
5. **Per-worker orchestration**: recover all of a worker's tasks, then
   `UNHEALTHY → DEAD`.
6. **Loop**: `run_forever`/`stop` (reuse established pattern).
7. **Unit tests.**
8. **Integration tests**, including the Scheduler/Dispatch re-consumption
   regression (§17).
9. **Full suite verification.**

## 22. Definition of Done

Phase 09 is complete when every checkbox in §18 passes against the live
Postgres container and skips cleanly without it, the full existing suite
(Phases 00–08) still passes, the `ASSIGNED → FAILED` domain addition is
explicitly confirmed and documented (not silently made), and no file
outside §15's list has been modified.
