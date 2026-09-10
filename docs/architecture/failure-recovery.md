# Failure Recovery

Outline how Atlas detects worker crashes and recovers tasks.

## Scenario
1. Worker A is processing Task X.
2. Worker A stops sending heartbeats.
3. Failure Detector marks Worker A as UNHEALTHY after timeout.
4. Recovery Manager identifies tasks in **RUNNING** state assigned to Worker A.
5. Those tasks are transitioned to **PENDING** and re‑queued.
6. Scheduler may assign them to another healthy worker.

## Race Conditions
- **Task Completion vs. Failure Detection**: If a worker finishes a task just as the detector times out, the system must ensure the final result is recorded before re‑queuing.
- **Idempotent State Updates**: All state transitions are performed within a database transaction to avoid duplicate reassignment.

The implementation will coordinate via the PostgreSQL transaction log and careful ordering of state changes.
