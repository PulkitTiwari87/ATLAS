# Phase 10 — gRPC

## 1. Objective

Replace Phase 06's temporary in-process transport
(`WorkerRegistry.get(worker_id) -> WorkerRuntime`, a direct Python method
call) with real gRPC, so workers can run as separate processes/machines,
**without** the Scheduler needing to know or care that the transport
changed. Per Phase 06 §24, this is a transport swap, not a redesign.

## 2. Scope

- A `proto/atlas.proto` file (does not currently exist — see §21 note).
- A gRPC server hosting the control-plane side of worker communication.
- A gRPC client used by worker processes in place of direct repository
  calls.
- **One resolved architecture gap** (§4) that `docs/api/grpc-api.md`
  leaves open — this plan's central finding.

## 3. Responsibilities

- **Scheduler/Dispatcher** (Phases 05/06, unchanged in shape): still
  decide/deliver exactly as before.
- **gRPC layer** (this phase): carries `RegisterWorker`, `Heartbeat`, task
  delivery, and `ReportTaskResult` between processes.
- **WorkerRuntime** (Phase 04 core logic, unchanged): still owns every
  state transition and the actual task execution.

## 4. The Push/Pull Gap — resolved, flagged for confirmation

Inspected `docs/api/grpc-api.md` and `proto/atlas.proto` (the latter does
not exist in the repository — see §21). The documented `WorkerService`:

```
rpc RegisterWorker (RegisterWorkerRequest) returns (RegisterWorkerResponse);
rpc Heartbeat (HeartbeatRequest) returns (HeartbeatResponse);
rpc ReportTaskResult (ReportTaskResultRequest) returns (ReportTaskResultResponse);
```

is entirely **worker-initiated** (unary request/response, worker calls
in). But Phase 06's `Dispatcher.dispatch(task, worker)` is
**control-plane-initiated** (the dispatcher decides and pushes) — there is
no RPC in the documented contract for the server to hand a task to a
worker. Plain unary gRPC cannot have the server call the client
unprompted; only a **stream** the worker keeps open allows the server to
push messages down it.

**Resolution proposed** (not yet implemented — flagged for your
confirmation before Phase 10 begins): add one new **bidirectional
streaming** RPC to `WorkerService`, e.g.
`rpc TaskChannel(stream WorkerMessage) returns (stream ServerMessage)`,
where:
- the worker opens the stream once, after `RegisterWorker` succeeds, and
  keeps it open for its lifetime;
- the server pushes a `DispatchTask` message down the stream exactly when
  `Dispatcher.dispatch()` (unchanged) decides to — this is the direct
  replacement for `WorkerRegistry.get(worker_id)`'s in-process call;
- the worker pushes `TaskResult` messages back up the same stream after
  `WorkerRuntime.execute_task()` (unchanged) returns.

`Heartbeat` and `RegisterWorker` stay exactly as documented (simple
unary calls) — only task delivery needs the new streaming RPC, because
only task delivery is server-initiated. **This is an addition to, not a
rewrite of, the documented contract** — `docs/api/grpc-api.md` would gain
one RPC, not be redesigned.

## 5. Worker Registration RPC

`RegisterWorker(RegisterWorkerRequest{hostname, address}) ->
RegisterWorkerResponse{worker_id}` — server-side handler calls the
*same, unchanged* `WorkerRepository.add` + `Worker.transition_to(AVAILABLE)`
logic Phase 04's `WorkerRuntime.register()` already has; the *worker
process* no longer calls PostgreSQL directly (a real architectural
improvement this phase legitimately introduces — see §15).

## 6. Task Dispatch (via the streaming RPC, §4)

- **Dispatcher input**: unchanged — a `(Task, Worker)` proposal from
  `Scheduler.run_cycle()`.
- **Worker invocation**: `Dispatcher.dispatch()`'s body changes from "call
  `runtime.execute_task(task.id)` locally" to "serialize `task.id` +
  `task.type` + `task.payload` into a `DispatchTask` message and push it
  onto that worker's open stream." The *signature* `dispatch(task,
  worker)` does not change (Phase 06 §24's contract, honored).
- **Execution lifecycle**: entirely server-side-of-the-worker-process —
  i.e. still `WorkerRuntime.execute_task()`, unchanged, just invoked from
  a gRPC stream-message handler instead of directly by a `Dispatcher`
  in the same process.
- **Result handling**: the worker process pushes a `TaskResult` (task id,
  final status, error if any) back up the stream once `execute_task()`
  returns; the control-plane-side stream handler does **not** re-persist
  anything — the worker process's own `WorkerRuntime.execute_task()` call
  already persisted everything (status, `TaskAttempt`, worker status) via
  its own direct DB connection, exactly as in Phase 04. The `TaskResult`
  message is purely informational, letting the control-plane's in-memory
  bookkeeping (e.g., "is this worker's stream currently busy") stay in
  sync — **it is not a second source of truth.**

## 7. Task Result Semantics

Single owner of persistence remains `WorkerRuntime._finish()` (Phase 04),
unchanged, running inside the worker's own process against its own DB
connection — matches `docs/architecture/system-overview.md`'s model of
workers as thin-but-DB-capable-for-their-own-work clients, and avoids
Phase 06/13's already-established "single owner for execution result
persistence" (Phase 06 §13) getting duplicated across a network boundary.

## 8. Heartbeat RPC

`Heartbeat(HeartbeatRequest{worker_id, timestamp}) -> HeartbeatResponse{}`
— unary, matches the doc exactly. Server-side handler calls the same
`WorkerRepository.update_heartbeat` Phase 07 already added. Worker-side:
`WorkerRuntime`'s heartbeat thread (Phase 07, unchanged in shape) calls
this RPC instead of the repository method directly, when running in
gRPC mode.

## 9. Error Handling / Deadlines

- Unary RPCs (`RegisterWorker`, `Heartbeat`): standard gRPC deadlines
  (a few seconds), standard status codes
  (`UNAVAILABLE` for transient failures, `INVALID_ARGUMENT` for malformed
  requests — validation hardening detail deferred to Phase 13).
- The streaming RPC: a dropped/broken stream is treated as "this worker
  is unreachable right now" by the control plane — **not** as "this
  worker is dead" (that's still Phase 08's job, based on missed
  heartbeats, unchanged). A dropped stream simply means
  `Dispatcher.dispatch()` cannot push to it; treated the same as today's
  "worker not found in `WorkerRegistry`" dispatch-failure case (Phase 06
  §9/§11.A) — reuse that outcome category, don't invent a new one.
- Worker-side reconnect: on stream failure, the worker retries opening a
  new `TaskChannel` stream with backoff — a small, self-contained piece
  of client logic, not a new distributed mechanism.

## 10. Connection Lifecycle / Graceful Shutdown

- `WorkerRuntime.shutdown()` (Phase 04/07, additive again): closes its
  `TaskChannel` stream (if open) as part of the existing `DRAINING → DEAD`
  sequence, alongside stopping the heartbeat thread (Phase 07 §12) — one
  more line added to an already-additive method, not a rewrite.
- Server-side: when a worker's stream closes (clean shutdown or drop),
  the server removes it from its in-memory "open streams" registry (the
  gRPC-mode replacement for Phase 06's `WorkerRegistry`) — that registry
  entry removal is the exact same conceptual operation as Phase 06's
  `WorkerRegistry.unregister()`, just triggered by a stream event instead
  of an explicit call.

## 11. Worker Identity

Unchanged — `Worker.id` (UUID), assigned client-side at construction
(Phase 04, `field(default_factory=uuid.uuid4)`) and confirmed by the
server's `RegisterWorkerResponse`. No new identity scheme.

## 12. Server / Client Responsibilities

- **Server** (control plane): hosts `WorkerService`; handlers call the
  *exact same* Phase 02 repositories and Phase 04/07/08/09 logic that
  already exists — gRPC handlers are thin adapters, not new business
  logic. `Dispatcher`/`Scheduler`/`FailureDetector`/`RecoveryManager` all
  run here, unchanged.
- **Client** (worker process): a thin `WorkerRuntime` variant (or the
  same class with its persistence/transport calls swapped based on a
  mode — exact class-splitting is an implementation-time decision, not
  fixed by this plan) that calls RPCs instead of repositories directly
  for `register`/heartbeat/task-delivery, while still running
  `execute_task()`'s actual claim-execute-persist logic against its own
  direct DB connection (§6/§7 — execution and result persistence stay
  local to the worker process, only *dispatch delivery* and
  *registration*/*heartbeat* go over gRPC).

## 13. Protobuf Message Design

Extends, does not replace, `docs/api/grpc-api.md`'s existing sketch:

```proto
message RegisterWorkerRequest  { string hostname = 1; string address = 2; }
message RegisterWorkerResponse { string worker_id = 1; }

message HeartbeatRequest  { string worker_id = 1; string timestamp = 2; }
message HeartbeatResponse {}

message DispatchTask { string task_id = 1; string task_type = 2; string payload_json = 3; }
message TaskResult   { string task_id = 1; string status = 2; string error = 3; }

message WorkerMessage { oneof kind { TaskResult result = 1; } }
message ServerMessage { oneof kind { DispatchTask dispatch = 1; } }

service WorkerService {
  rpc RegisterWorker(RegisterWorkerRequest) returns (RegisterWorkerResponse);
  rpc Heartbeat(HeartbeatRequest) returns (HeartbeatResponse);
  rpc TaskChannel(stream WorkerMessage) returns (stream ServerMessage);  // new, per §4
}
```

`payload_json`: `Task.payload` is a `dict` (JSONB in Postgres already) —
simplest correct wire representation is a JSON string field rather than
inventing a protobuf `Struct`/dynamic-typing scheme, consistent with
"smallest correct diff" and "no speculative abstraction."

## 14. Migration from Phase 06's Temporary Transport

| Phase 06 (today) | Phase 10 (this plan) |
|---|---|
| `WorkerRegistry` (in-process dict) | server-side open-stream registry (same shape, different payload) |
| `WorkerRegistry.get(worker_id)` | look up that worker's open `TaskChannel` stream |
| direct `runtime.execute_task(task.id)` call | push a `DispatchTask` message down the stream |
| `WorkerRuntime.register()` calling `WorkerRepository.add` directly | `WorkerRuntime.register()` calling the `RegisterWorker` RPC, whose *server-side handler* calls `WorkerRepository.add` |
| `Dispatcher.dispatch(task, worker)` | **unchanged signature**, different implementation inside |

`Scheduler` needs zero changes for this migration — confirmed, it never
referenced `WorkerRegistry` or any transport detail (Phase 05/06,
verified).

## 15. State Transitions

**None new.** Every `TaskStatus`/`WorkerStatus` transition remains exactly
where Phases 01/04/07/08/09 put it — gRPC is a transport, not a new
owner of any state change. This phase's only structural change is *where*
`WorkerRuntime`'s persistence calls physically run (worker process's own
DB connection vs. relayed through the server) — see §5/§7's note that
this is a legitimate, deliberate architectural clarification (workers
gain direct-but-limited DB access for their own execution results; the
server never re-derives or re-persists what a worker already reported).

## 16. Persistence Interaction

No new repository methods. Every persistence call this phase makes is one
already built in Phases 02/04/07: `WorkerRepository.add/update/
update_heartbeat`, `TaskRepository.get/update`, `TaskAttemptRepository.
add`. Only the *caller's process* changes for some of them (§5/§12), not
the methods themselves.

## 17. Transaction Boundaries

Unchanged from every prior phase — each persistence call already opens
and closes its own short `session_scope()`. gRPC adds a network hop
around these calls, not a new transaction shape.

## 18. Concurrency Model

- Server handles multiple workers' streams concurrently — gRPC's async
  server model (`grpcio`, already a declared dependency since Phase 00,
  unused until now) handles this; no new Atlas-level concurrency
  primitive is required beyond what `grpcio` already provides.
- Dispatch-to-stream-push concurrency mirrors Phase 06 §10's decision
  (sequential vs. threaded `dispatch_all`) — **whichever Phase 06 actually
  implemented carries forward unchanged**; this phase does not revisit
  that decision.

## 19. Failure Behavior

- RPC failure (`UNAVAILABLE`, deadline exceeded) on `RegisterWorker`:
  worker retries with backoff before it has a `worker_id` at all — no
  Atlas state exists yet for a worker that never successfully registered.
- RPC failure on `Heartbeat`: same as a missed heartbeat today (Phase 07
  §11) — logged, loop continues; Phase 08's threshold-based detection is
  what eventually reacts, unchanged.
- Stream drop mid-dispatch: treated as dispatch failure (§9), the
  existing Phase 06 category — no new failure type invented.
- **Still explicitly not implemented here**: heartbeat *interpretation*
  changes, retry policy changes, recovery changes — all unchanged from
  Phases 07–09.

## 20. Interaction with Previous/Next Phases

- Builds directly on Phase 06's `Dispatcher`/`WorkerRegistry` shape (§14)
  — no redesign.
- Builds directly on Phase 07/08's heartbeat mechanism — only the
  transport of `Heartbeat` changes, not its meaning.
- Phase 09's recovery logic is entirely unaffected — it operates on
  PostgreSQL state, which is unchanged in shape by this phase.
- Phase 11 (Observability) will want request/stream-level logging for the
  new RPCs — noted for that plan, not built here.

## 21. Files Expected to Change

New:
- `proto/atlas.proto` — **does not exist in the repository today**
  (confirmed by inspection); this phase creates it, based on
  `docs/api/grpc-api.md` plus the §4 addition.
- `src/atlas/grpc_server/` (or similar) — generated stubs (via
  `grpcio-tools`, already a dependency) + handler implementations.
- `src/atlas/worker/grpc_client.py` (or similar) — the worker-side RPC
  calls replacing direct repository/registry calls.
- Tests: `tests/grpc/test_registration.py`,
  `tests/grpc/test_task_channel.py`, `tests/grpc/test_heartbeat_rpc.py`.

Modified (additive, flagged per CLAUDE.md's "don't silently modify"):
- `src/atlas/worker/runtime.py` — the most significant single touch-point
  across Phases 07–10 combined: `register()`, heartbeat sending (Phase
  07), and now task-receiving all gain a gRPC-mode code path. Exact
  class-splitting (one `WorkerRuntime` with a transport strategy vs. a
  local/remote subclass split) is an implementation-time decision, not
  fixed here — **flagged for your input before Phase 10 begins**, since
  it's the largest structural change to Phase 04's file in the whole
  roadmap.
- `src/atlas/dispatch/dispatcher.py` / `registry.py` — internals change
  (§14) per Phase 06 §24's contract; public `dispatch()` signature does
  not.
- `docker-compose.yml` — this is the first phase where reintroducing a
  real `worker-1` service (per Phase 04's original docker-compose note,
  deferred since Phase 04 §18) finally makes sense, since workers can now
  genuinely run as separate processes/containers talking over gRPC.

Not touched: `src/atlas/domain/*`, `src/atlas/persistence/models.py`,
`src/atlas/scheduler/scheduler.py`, `src/atlas/api/*`,
`src/atlas/services/*`.

## 22. Dependencies

**None new** — `grpcio` and `grpcio-tools` are already declared in
`pyproject.toml` (since Phase 00) and have been unused until this phase.
No message broker, no service mesh, no Kubernetes.

## 23. Testing Strategy

**Unit**: protobuf message round-trip (serialize/deserialize), RPC
handler logic with a fake/in-memory gRPC channel (`grpcio`'s testing
utilities) — assert handlers call the correct existing repository/domain
methods, not reimplement them.

**Integration**: real Postgres + a real (in-process, same-machine) gRPC
server/client pair — full register → heartbeat → dispatch-via-stream →
execute → result flow, end-to-end, replacing Phase 06's direct-call
integration tests with the gRPC-transported equivalents. Stream-drop and
reconnect behavior.

**Regression**: full existing suite (Phases 00–09) passes unmodified
*in local/no-gRPC mode*, if the implementation keeps a local transport
path available for tests that don't need real gRPC (recommended, not
required by this plan — an implementation-time efficiency choice).

## 24. Acceptance Criteria

- [ ] A worker process can register over gRPC and appear `AVAILABLE` in
      PostgreSQL.
- [ ] Heartbeats sent over gRPC update `last_heartbeat` exactly as the
      Phase 07 direct-call version did.
- [ ] `Dispatcher.dispatch(task, worker)` delivers a task to a
      *separate-process* worker over the `TaskChannel` stream and the
      task executes to completion.
- [ ] `Scheduler` required zero code changes for this migration.
- [ ] Task/worker state transitions and `TaskAttempt` persistence are
      byte-for-byte the same as the Phase 04–09 direct-call behavior.
- [ ] A dropped stream is treated as a dispatch failure, not a worker-
      death determination (Phase 08 still owns that).
- [ ] No new PyPI dependency was added.
- [ ] All existing Phase 00–09 tests still pass (in whichever mode the
      implementation keeps available for them).

## 25. Explicit Out-of-Scope

- Redesigning Scheduler or Dispatch's decision logic.
- Message brokers.
- Observability/metrics (Phase 11).
- Hardening (malformed message validation beyond basic gRPC status
  codes, auth, deadlIne tuning) — Phase 13.
- Changing heartbeat *interpretation*, retry policy, or recovery logic.
- Multi-region/multi-cluster concerns.

## 26. Risks and Design Decisions

- **The push/pull gap (§4) is this plan's central, required decision** —
  flagged clearly, with a concrete proposed resolution (one new streaming
  RPC), not silently invented into the existing doc.
- **`WorkerRuntime`'s eventual split** (§21) is the largest single-file
  structural change anywhere in the roadmap — flagged for your input
  before implementation, not decided unilaterally here.
- **Late results under async transport** (flagged, not solved, in Phase
  09 §11) becomes directly relevant here — a `TaskResult` message could
  theoretically arrive after Phase 09 has already recovered that task.
  Recommended handling: the server-side stream handler checks the task's
  *current* status before trusting a late `TaskResult` as anything more
  than informational (since real persistence already happened worker-
  side) — flagged as a concrete Phase 10 design point, not fully resolved
  here since it depends on the class-split decision above.
- **Worker DB credentials**: giving worker processes their own direct
  PostgreSQL connection (§5/§7) is a real security-relevant decision —
  noted here, deferred to Phase 13 for any credential-scoping/hardening
  treatment.

## 27. Implementation Sequence

1. `proto/atlas.proto` (create), generate stubs via `grpcio-tools`.
2. Server-side unary handlers (`RegisterWorker`, `Heartbeat`) — thin
   adapters over existing repository calls.
3. Server-side `TaskChannel` stream handling + open-stream registry.
4. Worker-side client: register, heartbeat-over-gRPC, receive-dispatch-
   over-stream, still-local `execute_task()`.
5. `Dispatcher` internals updated to push over the stream registry
   instead of `WorkerRegistry` (signature unchanged, §14).
6. Unit tests.
7. Integration tests, end-to-end over real gRPC + real Postgres.
8. `docker-compose.yml`: reintroduce a real `worker-1` service.
9. Full suite verification.

## 28. Definition of Done

Phase 10 is complete when every checkbox in §24 passes, the streaming-RPC
resolution to the push/pull gap (§4) has been explicitly confirmed and
implemented (not left ambiguous), `WorkerRuntime`'s split (§21) has been
resolved deliberately rather than accreted ad hoc, the full existing test
suite (Phases 00–09) still passes, and no new dependency was added.
