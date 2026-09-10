# Atlas Implementation Roadmap

Atlas is implemented incrementally to preserve correctness and keep each change reviewable.

## Phases

- Phase 00 — Foundation
- Phase 01 — Domain Model
- Phase 02 — Persistence ([plan](phase-02-persistence.md))
- Phase 03 — Job API ([plan](phase-03-job-api.md))
- Phase 04 — Worker Runtime ([plan](phase-04-worker-runtime.md))
- Phase 05 — Scheduler ([plan](phase-05-scheduler.md))
- Phase 06 — Dispatch ([plan](phase-06-dispatch.md))
- Phase 07 — Heartbeats ([plan](phase-07-heartbeats.md))
- Phase 08 — Failure Detection ([plan](phase-08-failure-detection.md))
- Phase 09 — Recovery + Retries ([plan](phase-09-recovery-retries.md))
- Phase 10 — gRPC ([plan](phase-10-grpc.md))
- Phase 11 — Observability ([plan](phase-11-observability.md))
- Phase 12 — Integration + E2E Testing ([plan](phase-12-integration-e2e.md))
- Phase 13 — Hardening ([plan](phase-13-hardening.md))

Phases 07–13 are planned but not yet approved for implementation; each
plan explicitly flags the design decisions that need confirmation before
its phase begins (see each plan's "Risks and Design Decisions" section).

## Dependency Flow

Foundation
→ Domain Model
→ Persistence
→ Job API
→ Worker Runtime
→ Scheduler
→ Dispatch
→ Heartbeats
→ Failure Detection
→ Recovery + Retries
→ gRPC
→ Observability
→ Integration + E2E Testing
→ Hardening

Each phase should be completed, tested, and reviewed before proceeding to the next phase.

## Execution Rules

- Implement one phase at a time.
- Do not silently implement future phases.
- Preserve existing working behavior.
- Prefer the smallest correct implementation.
- Follow `CLAUDE.md`.
- Use Cavemen methodology.
- Use Ponytail Full coding principles.
- Add dependencies only when required.
- Update documentation when implementation materially changes the architecture.