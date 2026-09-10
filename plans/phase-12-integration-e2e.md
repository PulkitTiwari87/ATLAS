# Phase 12 — Integration + E2E Testing

## 1. Objective

Validate the full assembled system — Job API → Scheduler → Dispatch →
Worker → Heartbeat → Failure Detection → Recovery/Retry — end-to-end
against real PostgreSQL, using the real components built in Phases 03–10
composed together, not mocked. This phase adds **test infrastructure
only** — no production code changes.

## 2. Scope

- A reusable E2E test harness that boots real `Scheduler`,
  `DispatchLoop`, `FailureDetector`, `RecoveryManager`, and one or more
  `WorkerRuntime`s against the Phase 00 Postgres container.
- The 20 scenarios enumerated in your prompt, mapped to unit/integration/
  E2E tiers (§17).
- No new `src/atlas/` code — this phase is test-only, unless a scenario
  reveals a genuine bug in an earlier phase, in which case that's a fix
  to the phase that owns it, not new Phase 12 code (see §19).

## 3. Responsibilities

Phase 12 verifies; it does not implement. Every behavior under test was
already specified and built in Phases 03–10's own plans — this phase's
only job is composing the real components and observing real outcomes.

## 4. Architecture

`tests/e2e/harness.py`:
- `AtlasTestSystem`: a context manager that, on entry, starts
  `Scheduler`/`DispatchLoop`/`FailureDetector`/`RecoveryManager` each on
  its own background thread (`run_forever`, short poll intervals for
  fast tests) and registers N `WorkerRuntime`s into the (Phase 06/10)
  worker registry; on exit, calls `stop()` on every loop, joins threads,
  and calls `shutdown()` on every worker.
- Helper methods: `submit_job(name, priority, tasks)` (via `JobService`,
  Phase 03, unchanged), `wait_for_task_status(task_id, status,
  timeout)`, `wait_for_job_status(job_id, status, timeout)` — simple
  polling helpers, not new production code.

## 5. Inputs

Real PostgreSQL (Phase 00 Docker Compose service) — no fixtures beyond
what Phases 02–09 already established (`session_scope`, real
repositories).

## 6. Outputs

Pass/fail test results; no runtime artifacts beyond normal pytest output.

## 7. State Transitions

None new — this phase exercises exactly the transitions already owned by
Phases 01/04/05/07/08/09, verified together instead of in isolation.

## 8. Persistence Interaction

Uses existing repositories directly for setup/assertions (e.g. seeding a
worker with a stale heartbeat, or reading final task state) — no new
repository methods.

## 9. Transaction Boundaries

N/A — test code, not application code; individual assertions read
committed state via existing `session_scope()`-based repository calls.

## 10. Concurrency Model

The harness itself introduces threads (one per running loop, §4) — this
is test infrastructure, not a new Atlas concurrency primitive, and
mirrors exactly how a real deployment would run these loops (each as its
own long-lived process in production; as threads here purely for
single-process testability).

## 11. Failure Behavior

Scenarios explicitly test failure paths (17 below) — a scenario's
"failure" is the thing under test, not a test-infrastructure failure. If
the harness itself errors (e.g. can't reach Postgres), the test skips,
same pattern as every integration test since Phase 02.

## 12. Interaction with Previous Phases

Consumes Phases 03–09 (and 10, once built) as black boxes through their
already-defined public interfaces (`JobService`, `Scheduler.run_cycle`,
`Dispatcher.dispatch_all`, `WorkerRuntime.execute_task`/`register`/
`shutdown`, `FailureDetector.run_cycle`, `RecoveryManager.run_cycle`) —
no reaching into private methods.

## 13. Interaction with Next Phases

Phase 13's hardening priorities should be informed by whatever this phase
actually finds (per your Phase 13 instructions) — Phase 12 is the
evidence-gathering phase Phase 13 is supposed to react to.

## 14. API/Service Implications

None — no new API surface. `JobService` (Phase 03) is the only
service-layer entry point these tests use, unchanged.

## 15. Files Expected to Change

New only:
- `tests/e2e/__init__.py`
- `tests/e2e/harness.py` — `AtlasTestSystem` + helpers.
- `tests/e2e/test_full_lifecycle.py` — scenarios 1–9 (submission through
  multiple workers).
- `tests/e2e/test_failure_and_recovery.py` — scenarios 10–16 (heartbeat
  through duplicate-execution documentation).
- `tests/e2e/test_shutdown_and_staleness.py` — scenarios 17–20.

No `src/atlas/` changes are anticipated by this plan. If E2E testing
surfaces an actual defect in an earlier phase, fixing it means editing
that phase's file (e.g. a real bug in `RecoveryManager`), which is a bug
fix attributable to Phase 09, not new Phase 12 scope creep — noted so
it's not silently miscategorized later.

## 16. Dependencies

None new. `threading` (stdlib, for the harness) — same as already used
in Phase 05+'s own test suites (`tests/scheduler/test_ordering.py`
already does this).

## 17. Testing Strategy — scenario mapping

| # | Scenario | Tier |
|---|---|---|
| 1 | Job submission | Integration (`JobService`, existing Phase 03 tests already cover this — E2E only re-uses it as setup) |
| 2 | `SUBMITTED → QUEUED` | Integration (Phase 05, already covered) — E2E asserts it happens as a side effect of the full loop |
| 3 | Scheduler priority ordering | Unit (Phase 05, already covered) — not re-tested at E2E granularity |
| 4 | Dispatch | Integration (Phase 06, already covered) — E2E re-uses via the harness |
| 5 | Worker execution | Integration (Phase 04, already covered) |
| 6 | Successful task | **E2E** — full loop, real harness, assert terminal `SUCCEEDED` |
| 7 | Failed task | **E2E** — same, non-zero exit command |
| 8 | `TaskAttempt` persistence | Integration (Phase 04, already covered); E2E asserts it as part of 6/7 |
| 9 | Multiple workers | **E2E** — 2+ tasks, 2+ workers, assert both complete |
| 10 | Worker heartbeat | Integration (Phase 07, already covered) |
| 11 | Worker becomes stale | Integration (Phase 08, already covered) |
| 12 | Failure detection | Integration (Phase 08, already covered) |
| 13 | Task recovery | Integration (Phase 09, already covered) |
| 14 | Retry | **E2E** — kill a worker mid-task (stop its heartbeat thread without calling `shutdown()`, simulating a crash), run the full harness, assert the task is eventually re-executed and succeeds on a second worker |
| 15 | Retry exhaustion | **E2E** — same, with `max_retries=0`, assert terminal `FAILED` |
| 16 | Duplicate execution possibility | **E2E**, documentation-oriented: a task whose *first* attempt actually completes (e.g. writes a marker file) but whose worker is then killed before reporting; assert the task *does* re-execute (proving the documented Phase 09 §8 risk is real and observable, not just theoretical) — this test exists to prove the risk, not to prevent it |
| 17 | Stale scheduling/dispatch proposal | Integration (Phase 06, already covered) |
| 18 | Worker draining | Integration (Phase 04, already covered) — E2E: start a drain mid-cycle, assert no new task is assigned to that worker |
| 19 | Graceful shutdown | **E2E** — full harness shutdown, assert no exceptions, no orphaned threads |
| 20 | Full system startup/shutdown | **E2E** — the harness's own enter/exit is this scenario |

**Definition of unit vs. integration vs. E2E for this codebase** (stated
explicitly, per your instruction):
- **Unit**: no database, no real component composition — pure logic
  (ordering, eligibility, retry-decision functions).
- **Integration**: real Postgres, one component (or a component +
  the thing it directly calls, e.g. `Dispatcher` + a real
  `WorkerRuntime`) — the tier every phase from 02 onward has already used.
- **E2E** (new in this phase): real Postgres + multiple components
  running concurrently via `run_forever` loops, driven only through
  public entry points (`JobService`, not direct repository pokes, except
  for deliberately engineering a failure scenario like #14/#16).

## 18. Acceptance Criteria

- [ ] `AtlasTestSystem` harness starts and stops cleanly, with no
      orphaned threads after `__exit__`.
- [ ] Scenarios 6, 7, 9, 14, 15, 16, 18, 19, 20 (the ones marked E2E
      above) each have a passing test.
- [ ] All other scenarios are confirmed already covered by existing
      integration tests from their owning phase (cross-referenced, not
      redundantly re-implemented at E2E granularity).
- [ ] Scenario 16's test explicitly documents (in its docstring/assertion
      message) that duplicate execution is expected behavior, not a bug.
- [ ] Full existing suite (Phases 00–11) still passes unmodified.

## 19. Explicit Out-of-Scope

- Any new production code (§15) — if one is found necessary, it belongs
  to whichever phase owns that behavior, not Phase 12 itself.
- Load/performance/stress testing.
- Multi-machine deployment testing (still single-process-with-threads,
  same as every phase's own integration tests).
- Chaos-engineering-style fault injection beyond the specific scenarios
  listed (killing one worker's heartbeat thread) — not a general fault-
  injection framework.

## 20. Risks and Design Decisions

- **Test flakiness from timing** (`run_forever` poll intervals, `wait_for
  _*` polling helpers): mitigated by short poll intervals and generous
  but bounded timeouts in the harness — not solved with sleeps hard-coded
  per test.
- **Scenario 16 is deliberately the "prove the risk" test**, not a "fix
  the risk" test — flagged clearly so it's never mistaken for a
  regression when duplicate execution is observed; that IS the expected
  outcome.
- **Harness complexity**: kept to the minimum needed to start/stop four
  loops and N workers — not a general-purpose test framework.

## 21. Implementation Sequence

1. `AtlasTestSystem` harness (start/stop, no scenarios yet).
2. `wait_for_*` polling helpers.
3. Scenarios 6, 7, 8, 9 (happy-path multi-worker).
4. Scenarios 10–13 cross-reference check (confirm existing coverage,
   don't duplicate).
5. Scenarios 14, 15, 16 (failure/retry/duplicate-execution).
6. Scenarios 17, 18 cross-reference + drain check.
7. Scenarios 19, 20 (shutdown).
8. Full suite verification.

## 22. Definition of Done

Phase 12 is complete when every checkbox in §18 passes, the harness is
reusable (not scenario-specific), the full existing suite (Phases 00–11)
passes unmodified, and no production code exists that wasn't already
attributable to an earlier phase's plan.
