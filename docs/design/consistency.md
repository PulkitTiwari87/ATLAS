# Consistency

Atlas relies on PostgreSQL as the single source of truth. All state transitions (job, task, worker) must be performed within a database transaction to guarantee atomicity and isolation.

- **Read‑Write Consistency** – When a component reads a state, it must see the latest committed version.
- **Write‑After‑Read** – Updates must be based on the current state to avoid lost updates.
- **Idempotent Updates** – Where possible, use `ON CONFLICT DO UPDATE` or version columns to prevent duplicate state changes.

The consistency model is **eventual consistency** for in‑memory caches (if any) but **strong consistency** for persisted state.
