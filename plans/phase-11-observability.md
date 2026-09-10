# Phase 11 — Observability

## 1. Objective

Make Atlas debuggable — able to answer "what happened to job X / task Y /
worker Z, and when" from logs and existing PostgreSQL data — without
introducing a monitoring platform. No architecture doc (`system-overview
.md`, `component-architecture.md`, `communication.md`) mentions
Prometheus, Grafana, or OpenTelemetry; none is added here absent a
concrete need.

## 2. Scope

- Structured, correlated logging across every component built in
  Phases 03–10 (currently ad hoc `logger.exception(...)` calls with no
  consistent fields).
- A small `LogContext` helper (stdlib `logging`, no new dependency).
- Optional, minimal in-process counters exposed via the existing FastAPI
  app (`/metrics`, plain text or JSON) — only if trivially implementable
  with stdlib; not a Prometheus exporter.

## 3. Responsibilities

Every component logs its own decisions with consistent identifying
fields; this phase does not change what any component *decides* — purely
additive visibility.

## 4. Architecture

- `src/atlas/observability/logging.py`: a small helper building on
  `atlas.logging.setup_logging` (Phase 00, unchanged) — e.g. a
  `log_event(logger, event, **fields)` function that emits one structured
  line (`extra={...}` or a formatted key=value string) per significant
  event, rather than free-text messages.
- Natural correlation keys already exist in the domain model — `job_id`,
  `task_id`, `worker_id`, `attempt` — no new ID type invented.

## 5. Inputs

Existing domain objects already flowing through every component
(`Job`, `Task`, `Worker`, `TaskAttempt`) — this phase reads their
identifying fields for logging, nothing more.

## 6. Outputs

- Structured log lines at key transition points:
  - `JobService`: job created, cancelled.
  - `Scheduler`: cycle summary (runnable count, proposals count, jobs
    queued).
  - `Dispatcher`: dispatch attempted/succeeded/skipped (with reason).
  - `WorkerRuntime`: task claimed, started, succeeded, failed; heartbeat
    started/stopped.
  - `FailureDetector`: worker marked `UNHEALTHY` (with time-since-last-
    heartbeat).
  - `RecoveryManager`: task recovered (retried vs. terminal), worker
    marked `DEAD`.
- Optionally, `GET /metrics` (FastAPI, existing app) returning simple
  counters (jobs created, tasks succeeded/failed, workers registered) —
  computed from a lightweight in-memory counter dict, or, more in
  keeping with "PostgreSQL is the source of truth," computed on read
  directly from existing tables (`SELECT status, COUNT(*) FROM tasks
  GROUP BY status`, etc.) — **preferred**, since it needs no new state
  and can't drift from reality. Recommend this over in-memory counters.

## 7. State Transitions

None. This phase changes no domain behavior.

## 8. Persistence Interaction

None new for logging. The optional `/metrics` endpoint (if built as
DB-derived, §6) reuses existing repositories'
`list_by_status`/`list_all` methods — read-only, no new repository
method needed for the counts explicitly listed above (they're all
`GROUP BY status` shaped, answerable by iterating existing
`list_by_status` results per status, or, if that proves too slow at
scale, a small dedicated count query — not decided here, deferred to
implementation with evidence).

## 9. Transaction Boundaries

Unaffected — logging never opens a transaction; `/metrics` (if DB-backed)
uses the existing `session_scope()` read pattern, one short read.

## 10. Concurrency Model

Python's `logging` module is already thread-safe (relevant given Phase
07's heartbeat thread and Phase 06/10's potential dispatch concurrency) —
no new synchronization needed.

## 11. Failure Behavior

A logging call must never raise into calling code — wrap `log_event` in
a try/except that swallows and prints to stderr as a last resort (logging
itself failing must not crash a scheduling/dispatch/recovery cycle).

## 12. Interaction with Previous Phases

Touches every component built in Phases 03–10 (additive log calls only —
see §15). Does not change any component's control flow, return values,
or persisted state.

## 13. Interaction with Next Phases

Phase 12's E2E tests will likely assert on log output or `/metrics`
counts as one way of verifying scenarios occurred — noted for that plan.
Phase 13 may add structured fields for security-relevant events
(rejected payloads, auth failures) once those exist.

## 14. API/Service Implications

One new optional endpoint, `GET /metrics`, added to `src/atlas/main.py`
the same way `/health` already is — no new router package needed for one
endpoint (avoids the fragmentation `CLAUDE.md` §17 warns against).

## 15. Files Expected to Change

New:
- `src/atlas/observability/__init__.py`
- `src/atlas/observability/logging.py` — `log_event` helper.
- `tests/observability/test_logging.py`

Modified (additive log calls only, no logic changes):
- `src/atlas/services/job_service.py`
- `src/atlas/scheduler/scheduler.py`
- `src/atlas/dispatch/dispatcher.py`
- `src/atlas/worker/runtime.py`
- `src/atlas/failure_detection/detector.py`
- `src/atlas/recovery/recovery.py`
- `src/atlas/main.py` — optional `/metrics` route.

This is a wide but shallow touch — every file gets a handful of added log
calls, nothing structural.

## 16. Dependencies

None. Stdlib `logging` only. Explicitly **not** adding
`prometheus-client`, `opentelemetry-*`, or any APM agent — no
architecture document requires them, and CLAUDE.md's dependency rule
requires a concrete reason before adding one. If metrics needs grow
beyond simple counts later, that's a future decision made with evidence,
not now.

## 17. Testing Strategy

**Unit**: `log_event` emits the expected structured fields; a logging
failure (e.g. a bad `extra` key) doesn't raise.

**Integration**: `/metrics` (if built) returns correct counts against
real Postgres fixtures (a few jobs/tasks in known states).

**Regression**: full existing suite (Phases 00–10) passes unmodified —
the whole point of this phase is that no existing test's assertions
should need to change, since no behavior changes.

## 18. Acceptance Criteria

- [ ] Every major event listed in §6 produces a structured log line with
      the relevant correlation id(s).
- [ ] A logging failure never propagates into calling code.
- [ ] `/metrics` (if implemented) returns accurate counts against live
      DB state.
- [ ] No component's decisions, return values, or persisted state changed
      as a result of this phase.
- [ ] No new dependency added.
- [ ] All existing Phase 00–10 tests still pass unmodified.

## 19. Explicit Out-of-Scope

- Prometheus/Grafana/OpenTelemetry or any external monitoring platform.
- Distributed tracing.
- Alerting.
- Log aggregation infrastructure (ELK, etc.) — logs go to stdout, same as
  every phase so far; operators pipe them wherever they like.
- Any change to business logic, retry policy, or state machines.
- Authentication/authorization for `/metrics`.

## 20. Risks and Design Decisions

- **DB-derived `/metrics` vs. in-memory counters**: recommended DB-
  derived (§6) specifically to avoid a second, driftable source of truth
  — consistent with ADR-002's single-source-of-truth principle. Flagged
  as the recommended default, not mandatory.
- **Log volume**: per-heartbeat logging (every few seconds, per worker)
  could get noisy at scale — recommend logging heartbeat *starts/stops*,
  not every tick, keeping steady-state log volume low. A tick-level debug
  log is fine gated behind `LOG_LEVEL=debug` (existing config, Phase 00).

## 21. Implementation Sequence

1. `log_event` helper.
2. Wire into `JobService`, `Scheduler`.
3. Wire into `Dispatcher`, `WorkerRuntime`.
4. Wire into `FailureDetector`, `RecoveryManager`.
5. Optional `/metrics` endpoint.
6. Unit tests.
7. Integration test for `/metrics` (if built).
8. Full suite verification (must show zero behavioral diffs).

## 22. Definition of Done

Phase 11 is complete when every checkbox in §18 passes, the full existing
suite (Phases 00–10) passes with no assertion changes required anywhere,
and log output is sufficient to reconstruct a job's/task's/worker's
history from stdout alone for a debugging session.
