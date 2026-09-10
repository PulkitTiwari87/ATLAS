# Phase 13 — Hardening

## 1. Objective

Address the concrete, already-identified risks deferred across Phases
04–12's own plans — not invent new speculative production features. Every
item in §6 below traces back to a specific line in an earlier phase's
plan; this document does not introduce risks no prior plan already named.

## 2. Scope

Fixes only for risks with a named source (§6). Anything without one is
listed separately as optional future work (§7) and explicitly not
required for Phase 13 completion.

## 3. Responsibilities

Phase 13 does not add features. It closes gaps already on the record.

## 4. Architecture

No new components. Changes are localized to the files already owning
each risk (e.g. subprocess timeout tuning stays in
`src/atlas/worker/runtime.py`, not a new "security" module, unless a
single item genuinely needs its own file — decided per-item in §15, not
assumed).

## 5. Inputs

The actual findings from Phase 12's E2E runs (per your instruction:
*"Hardening must be based on actual risks discovered during Phases
06–12"*) plus every risk already flagged in Phases 04–10's own "Risks and
Design Decisions" sections — this document is a synthesis of those, not a
fresh brainstorm.

## 6. Required Hardening (traced to a specific prior finding)

| # | Item | Source | Fix |
|---|---|---|---|
| 1 | Shell executor has no sandboxing/resource limits, only a timeout | Phase 04 §18 ("deferred to Phase 13"), Phase 06 §20 | Document the limitation clearly in code/docs; add basic resource guards (e.g. `ulimit`-style constraints via `subprocess.run`'s `preexec_fn` on POSIX, or explicitly document Windows has none) — **not** full containerization (that would be a Redis/Kubernetes-adjacent infra addition, explicitly out of scope for Atlas per `CLAUDE.md` §16). |
| 2 | `_run_shell`'s `task.payload["command"]` raises an unguarded `KeyError` if `payload` is malformed (missing `"command"`) | Not previously flagged, but directly observable in `src/atlas/worker/runtime.py` today | Validate payload shape before executing; a malformed payload should produce a normal `FAILED` outcome with a clear error message, not an unhandled `KeyError` bubbling through `execute_task`'s existing `except Exception` (it's already caught there, but the error message is unhelpfully generic — improve it). |
| 3 | Worker DB credentials become a real concern once workers run as separate processes | Phase 10 §26 | Scope worker-process DB credentials to least privilege (e.g. a Postgres role that can only touch `tasks`/`task_attempts`/its own `workers` row) — a configuration/deployment change, not new application code. |
| 4 | gRPC malformed-message / deadline handling was only sketched, not hardened | Phase 10 §9/§19 | Add explicit `INVALID_ARGUMENT` validation on RPC inputs (empty `worker_id`, malformed timestamps) and confirm deadlines are enforced on every unary call, not just documented. |
| 5 | Late `TaskResult` after Phase 09 has already recovered a task | Phase 10 §26 | Server-side stream handler checks the task's current status before treating a late result as anything beyond informational (Phase 10 §26 already sketches this — Phase 13 is where it's actually implemented and tested, if Phase 10 left it as a documented gap). |
| 6 | False-positive `UNHEALTHY` marking has no recovery path back to `AVAILABLE` | Phase 08 §20, Phase 09 §20 | Evaluate whether this is worth addressing at all — see §7, this may be accepted as permanent-by-design rather than "hardened," since fixing it would mean adding a new state-machine edge, which is a real design decision requiring the same explicit-confirmation treatment as Phase 09 §6's edge, not a Phase-13-default change. |
| 7 | Log volume / heartbeat log noise | Phase 11 §20 | Confirm the recommended mitigation (log start/stop, not every tick) was actually followed; tune if Phase 12's E2E runs showed excessive output. |
| 8 | Configuration validation (e.g. `MAX_RETRIES` negative, `WORKER_TIMEOUT` zero) | Not previously flagged; a natural gap given `Settings` (Phase 00) does no validation at all | Add basic sanity checks in `get_settings()` (reject obviously-invalid values) — small, contained, stdlib-only. |
| 9 | Concurrency/stale-state edge cases actually observed during Phase 12 | Phase 12 (whatever it finds) | Cannot be enumerated before Phase 12 runs — this row is a placeholder acknowledging that Phase 12's actual findings supersede/extend this table, not a promise of specific fixes decided now. |

## 7. Optional Future Work (explicitly not required for Phase 13 completion)

- Full task sandboxing / containerized execution — a genuine
  architecture change (would need a design doc and ADR of its own,
  arguably crosses into "new infrastructure" territory `CLAUDE.md` §16
  gates).
- Worker authentication/mTLS for gRPC — not required by any existing doc;
  add only if a real deployment need arises.
- Distributed rate limiting / backpressure — no current evidence Atlas
  needs it at its documented scale.
- Idempotency-key mechanism in the API (`docs/design/idempotency.md`
  already marks this "future work" independent of this phase).
- Any metrics/alerting beyond Phase 11's scope.

## 8. State Transitions

None new, **except** the item-6 question (§6, row 6) — if resolved as
"add a recovery path," that would be a new `UNHEALTHY → AVAILABLE`
(or similar) edge requiring the same flagged-before-implementing
treatment as every other domain change in this roadmap. Not decided by
this plan; deferred to an explicit decision at Phase 13 implementation
time, informed by whatever Phase 12 actually observed.

## 9. Persistence Interaction

Item 2 (payload validation) and item 8 (config validation) need no new
repository methods — they're input-validation changes at existing call
sites.

## 10. Transaction Boundaries

Unaffected by any item in §6.

## 11. Concurrency Model

Unaffected, unless Phase 12 surfaces a specific race not already covered
by an existing state-machine guard — not anticipated by this plan absent
evidence.

## 12. Failure Behavior

Every item in §6 is itself about improving failure behavior at the
margins (clearer errors, input validation, resource bounds) — no new
failure category is introduced.

## 13. Interaction with Previous Phases

This phase touches files owned by nearly every previous phase (worker
runtime, gRPC layer, config, observability) but only at the margins named
in §6 — no phase's core design is revisited.

## 14. Interaction with Next Phases

None — Phase 13 is the roadmap's last phase.

## 15. Files Expected to Change

- `src/atlas/worker/runtime.py` — items 1, 2.
- `src/atlas/config.py` — item 8.
- `src/atlas/grpc_server/*` / `src/atlas/worker/grpc_client.py` (Phase
  10's files) — items 4, 5.
- `src/atlas/observability/logging.py` (Phase 11's file) — item 7, if
  needed.
- Deployment/docs (not application code) — item 3.
- Tests for each of the above.

No new top-level package anticipated — every item extends an existing
file. If Phase 12's findings (item 9) reveal something needing a new
component, that would be identified at implementation time, not
speculated here.

## 16. Dependencies

None anticipated. Every item in §6 is achievable with stdlib +
already-declared dependencies.

## 17. Testing Strategy

One test per §6 item, added to the existing test file for whichever
component it touches (e.g. a malformed-payload test in
`tests/worker/test_task_execution.py`) — no new test tier introduced.
Regression: full existing suite (Phases 00–12) passes unmodified.

## 18. Acceptance Criteria

- [ ] Every §6 item has either a fix + test, or an explicit documented
      decision to defer it (item 6's case).
- [ ] No §7 item was implemented without a new, separate confirmation
      (this phase does not silently expand its own scope).
- [ ] Full existing suite (Phases 00–12) still passes.

## 19. Explicit Out-of-Scope

Everything in §7, plus anything not traceable to §5's sources — this
phase does not "add hardening" generically; it closes named gaps only.

## 20. Risks and Design Decisions

- **The temptation to over-scope**: hardening phases are where projects
  often accrete unbounded "while we're at it" work. This plan pushes back
  on that explicitly by requiring every required item to trace to a
  specific prior finding (§6's "Source" column) — anything without one
  goes to §7, not §6.
- **Item 6 is a real open design question**, not a simple fix — flagged
  as such rather than resolved unilaterally here.

## 21. Implementation Sequence

1. Triage: re-confirm §6 against actual Phase 12 findings (some rows may
   turn out unnecessary; new ones may appear from row 9).
2. Fix items in dependency order: config validation (8) → payload
   validation (2) → shell resource guards (1) → gRPC validation (4) →
   late-result handling (5) → credential scoping (3, deployment-only) →
   log tuning (7).
3. Tests per item.
4. Full suite verification.

## 22. Definition of Done

Phase 13 — and the roadmap — is complete when every §6 item is resolved
(fixed or explicitly deferred with reasoning) with a passing test, no §7
item was added without separate confirmation, and the full test suite
(Phases 00–12) still passes.
