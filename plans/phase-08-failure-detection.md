# Phase 08 — Failure Detection

## 1. Objective

Interpret the *absence* of a recent heartbeat (Phase 07's sole output) and
mark a worker `UNHEALTHY`. Nothing more. Detection only — no recovery, no
reassignment, no retry accounting.

## 2. Scope

- `FailureDetector`: a polling loop, same shape as `Scheduler`/
  `DispatchLoop` (`run_cycle`, `run_forever`, `stop`).
- One state transition owned: `AVAILABLE → UNHEALTHY` and
  `BUSY → UNHEALTHY` (both already exist in the Phase 01 `WorkerStatus`
  machine, unused until now).

## 3. Responsibilities

- **Phase 07** (already planned): records liveness.
- **Phase 08** (this plan): interprets missing liveness → `UNHEALTHY`.
- **Phase 09**: recovers the affected tasks and retires the worker to
  `DEAD`.

This plan strictly stays inside the middle box.

## 4. Architecture

- `src/atlas/failure_detection/detector.py`, `FailureDetector`.
- `run_cycle()`: load `AVAILABLE` + `BUSY` workers (two calls to the
  existing `WorkerRepository.list_by_status` — no new repository method
  needed, same "filter in Python" pattern `Scheduler` already uses for
  priority lookups), filter to those whose `last_heartbeat` is `None` or
  older than the stale threshold (§5), and transition each to `UNHEALTHY`.
- `run_forever(poll_interval_seconds)` / `stop()`: same pattern as
  `Scheduler`/`DispatchLoop` — reused, not reinvented.

## 5. Stale Threshold — resolving a real doc/config mismatch

`docs/design/heartbeats.md` states: *"timeout – If `now - last_seen >
HEARTBEAT_INTERVAL * 2`, worker is considered UNHEALTHY."* With the
actual configured defaults (`HEARTBEAT_INTERVAL=5`,
`WORKER_TIMEOUT=30`, from `.env.example`/`src/atlas/config.py`, both
already present since Phase 00), that formula gives a 10-second threshold
— but `WORKER_TIMEOUT` is a *separate*, already-named config value
(`settings.worker_timeout`, default `30`) whose entire documented purpose
(Phase 00's plan: *"Worker timing values"*) is exactly this threshold.

**Resolution: use `settings.worker_timeout` directly as the staleness
threshold, not `heartbeat_interval * 2`.** Per `CLAUDE.md` §1's source-of-
truth order (existing code/config ranks above docs when they disagree),
a dedicated, already-configured value takes precedence over a formula in
a design doc that predates it and was never wired to any actual setting.
**Flagging this explicitly as a documentation/config inconsistency** —
`docs/design/heartbeats.md`'s formula should be corrected to say
"`now - last_heartbeat > WORKER_TIMEOUT`" when Phase 08 is implemented
(a factual doc correction, per `CLAUDE.md` §18, not a redesign).

## 6. Detection Interval

`run_forever(poll_interval_seconds=1.0)` — a constructor default, same
non-new-Settings-field precedent as Scheduler/DispatchLoop (Phase 05 §8,
Phase 06 §14). No new `.env` variable.

## 7. Stale-Worker Query

No new repository method (§4). For each candidate worker:
`worker.last_heartbeat is None or (now - worker.last_heartbeat) >
timedelta(seconds=settings.worker_timeout)`.

`last_heartbeat is None` covers a worker that registered but Phase 07's
heartbeat thread never got a chance to tick yet (or, hypothetically, a
worker not running Phase 07 code at all) — treated as stale immediately
rather than trusted indefinitely, matching the conservative "assume
liveness must be proven" posture implied by the whole heartbeat design.

## 8. State Transition

```
AVAILABLE --(FailureDetector, new)--> UNHEALTHY
BUSY      --(FailureDetector, new)--> UNHEALTHY
```

Both edges already exist in `src/atlas/domain/worker.py`'s
`_ALLOWED_TRANSITIONS` (Phase 01, unused until now) — **no domain
modification required.** `UNHEALTHY → DEAD` (also pre-existing) is
explicitly **not** driven by this phase — see §12/Phase 09.

## 9. Persistence Interaction

Reused as-is: `WorkerRepository.list_by_status`, `WorkerRepository.get`,
`WorkerRepository.update` (Phase 02/04). `update()` here is safe to use
in its full-row form (unlike the heartbeat thread, §9 of Phase 07) because
the detector is the only writer of `status` transitions to `UNHEALTHY`,
and it always re-fetches the worker fresh inside its own transaction
immediately before transitioning (§11) — no stale-field risk.

## 10. Transaction Boundaries

One short transaction per candidate worker: re-fetch the worker fresh,
re-check staleness and current status (defense against the race in §11),
transition, update, commit. Never one transaction for the whole cycle —
matches the per-entity-transaction pattern already used by
`Scheduler._advance_submitted_jobs`.

## 11. Concurrency Model

- **Race with heartbeat** (Phase 07): the detector might read a
  `last_heartbeat` a moment before a concurrent heartbeat tick refreshes
  it. Accepted, not solved — inherent to any timeout-based liveness
  check, and self-correcting on the next detector cycle if the worker
  really is alive. Phase 07's `update_heartbeat` (narrow write) doesn't
  change this either way.
- **Race with dispatch** (Phase 06): a worker could be mid-claim
  (`WorkerRuntime._assign`, `AVAILABLE → BUSY`) at the same moment the
  detector tries `AVAILABLE → UNHEALTHY`. Both are guarded by
  `transition_to`'s existing `InvalidTransitionError` — whichever
  transaction commits first wins; the loser's caller handles it
  gracefully already: if dispatch loses, `Dispatcher.dispatch()`'s
  existing `InvalidTransitionError` catch (Phase 06 §9) treats it as a
  stale proposal — **no new Dispatch-side handling required.** If the
  detector loses (worker just became `BUSY`), the detector simply
  re-fetches fresh state next cycle — it does not treat this as an error,
  since re-checking `worker.status in {AVAILABLE, BUSY}` immediately
  before transitioning (§10) means a `BUSY` result is still a valid
  target for `UNHEALTHY` anyway (both `AVAILABLE` and `BUSY` route to
  `UNHEALTHY`).
- **Race with shutdown**: same guard — `AVAILABLE → DRAINING` (shutdown)
  vs. `AVAILABLE → UNHEALTHY` (detector) racing on the same worker; one
  wins, the other gets `InvalidTransitionError`, caught per-worker and
  logged, not allowed to abort the whole detection cycle (same
  "one bad item shouldn't kill the loop" pattern as `Scheduler.
  run_forever`'s exception handling, applied per-worker instead of
  per-cycle here since a cycle processes many workers).
- **No new locking.** PostgreSQL's implicit row-level behavior inside
  each short transaction, plus the existing state-machine guard, are
  sufficient — matches the "prefer PostgreSQL transactional guarantees"
  instruction directly.

## 12. Interaction with Previous Phases

- Reads `Worker.last_heartbeat` (Phase 07's output) and `WorkerStatus`
  (Phase 01/04).
- Does **not** touch `Task` state at all — an `UNHEALTHY` worker's
  in-flight tasks are left exactly as they are (still `RUNNING`/
  `ASSIGNED`, still pointing at this now-`UNHEALTHY` worker via
  `Task.worker_id`) until Phase 09 acts on them. This is the precise
  handoff point between Phase 08 and Phase 09.
- Scheduler (Phase 05) is unaffected: it only ever selects `AVAILABLE`
  workers, so an `UNHEALTHY` worker simply stops being proposed —
  automatically, with no Phase 08 code needing to talk to Scheduler.
- **No modification to `src/atlas/worker/runtime.py` or
  `src/atlas/scheduler/scheduler.py` or `src/atlas/dispatch/*`.**

## 13. Interaction with Next Phases

Phase 09's `RecoveryManager` is the consumer: it queries for
`WorkerStatus.UNHEALTHY` workers (reusing `list_by_status`, no new
method) and recovers their orphaned tasks, eventually transitioning them
`UNHEALTHY → DEAD`. Phase 08 produces exactly the input Phase 09 needs
and nothing else.

## 14. API/Service Implications

None. No REST/gRPC surface.

## 15. Files Expected to Change

New:
- `src/atlas/failure_detection/__init__.py`
- `src/atlas/failure_detection/detector.py` — `FailureDetector`.
- `tests/failure_detection/test_detector.py` (unit)
- `tests/failure_detection/test_detector_integration.py` (integration)

Not touched: everything from Phases 00–07 (domain, persistence,
worker, scheduler, dispatch, api, services). `docker-compose.yml` stays
Postgres-only.

## 16. Dependencies

None. Stdlib only (`datetime`, `time`, `logging`).

## 17. Testing Strategy

**Unit** (`tests/failure_detection/test_detector.py`, no database —
construct `Worker` objects directly):
- worker with `last_heartbeat` older than `worker_timeout` → flagged
  stale.
- worker with recent `last_heartbeat` → not flagged.
- worker with `last_heartbeat is None` → flagged stale.
- `DRAINING`/`DEAD`/`REGISTERING`/`UNHEALTHY` workers are never
  candidates (only `AVAILABLE`/`BUSY` are queried at all).
- `run_forever`/`stop` shutdown behavior (same pattern as
  `tests/scheduler/test_ordering.py`).
- one worker's transition failure (simulated `InvalidTransitionError`)
  doesn't stop the rest of the cycle from processing other workers.

**Integration** (real Postgres, same skip pattern):
- a registered worker with an old `last_heartbeat` (set directly via
  `WorkerRepository.update` in the test, bypassing the Phase 07 thread
  for determinism) → after `run_cycle()`, status is `UNHEALTHY` in the
  DB.
- a worker with a fresh heartbeat is untouched after `run_cycle()`.
- a `BUSY` stale worker (task still `RUNNING`, unmodified by this phase)
  → worker becomes `UNHEALTHY`; the task's status and `worker_id` are
  verified **unchanged** (proves the Phase 08/09 boundary holds).
- race regression: mark a worker `UNHEALTHY` at the same moment
  `WorkerRuntime.execute_task()` is claiming it in another thread/test
  step — assert no unhandled exception, no corrupted final state (one of
  the two transitions wins cleanly).

**Regression**: full existing suite (Phases 00–07) passes unmodified.

Not included: any recovery, reassignment, or retry test — Phase 09.

## 18. Acceptance Criteria

- [ ] A worker whose `last_heartbeat` exceeds `settings.worker_timeout`
      is transitioned to `UNHEALTHY` (from either `AVAILABLE` or `BUSY`).
- [ ] A worker with a recent heartbeat is left untouched.
- [ ] No `Task` row is ever modified by `FailureDetector`.
- [ ] `REGISTERING`/`DRAINING`/`DEAD`/`UNHEALTHY` workers are never
      candidates.
- [ ] The detector loop tolerates a single worker's transition race
      without aborting the cycle.
- [ ] `run_forever()`/`stop()` behave like `Scheduler`'s equivalent
      methods (started, stopped cleanly, survives per-cycle exceptions).
- [ ] No new `WorkerStatus` value was added.
- [ ] All existing Phase 00–07 tests still pass unmodified.

## 19. Explicit Out-of-Scope

- Task reassignment (Phase 09).
- Retry policy / `Task.attempt` modification (Phase 09).
- Worker recovery / `UNHEALTHY → DEAD` transition (Phase 09).
- Task execution of any kind.
- gRPC (Phase 10).
- Any new `WorkerStatus` or `TaskStatus` value.

## 20. Risks and Design Decisions

- **Doc/config mismatch resolved** (§5) — using `settings.worker_timeout`
  over `heartbeats.md`'s `HEARTBEAT_INTERVAL * 2` formula. Flagged for
  your confirmation before implementation, since it's a real, user-
  visible behavioral choice (a 30s vs. 10s default detection window).
- **False positives under clock/scheduling jitter**: a worker that's
  technically alive but briefly delayed (GC pause, CPU contention) could
  be marked `UNHEALTHY` prematurely. Accepted — no grace-period tuning
  beyond the existing `WORKER_TIMEOUT` value; revisit only with evidence.
- **`UNHEALTHY` is not a dead end for the worker in principle** (the
  state machine allows only `UNHEALTHY → DEAD`, not back to `AVAILABLE`)
  — meaning a worker that *was* actually still alive and gets falsely
  marked `UNHEALTHY` cannot self-heal back to `AVAILABLE` in the current
  state machine. This is an existing Phase 01 design constraint, not
  introduced here; flagged for awareness since Phase 08 is the first
  phase to actually exercise it.

## 21. Implementation Sequence

1. `FailureDetector.run_cycle()`: load candidates, filter staleness (pure
   logic, testable without a full loop).
2. Per-worker transition + persistence, with the race-tolerant try/except
   (§11).
3. `run_forever`/`stop` (reuse the established pattern).
4. Unit tests.
5. Integration tests, including the race regression.
6. Full suite verification.

## 22. Definition of Done

Phase 08 is complete when every checkbox in §18 passes against the live
Postgres container and skips cleanly without it, the full existing suite
(Phases 00–07) still passes, and no file outside §15's new-files list has
been modified — plus the one factual correction to
`docs/design/heartbeats.md`'s threshold formula (§5).
