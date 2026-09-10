# ADR-003 – At‑Least‑Once Execution

**Status:** Proposed

## Context

Distributed task execution inevitably faces the possibility of duplicate execution due to retries, worker crashes, or network partitions. Achieving exactly‑once semantics requires complex coordination (e.g., two‑phase commit, idempotent side‑effects) which would add considerable complexity to the initial version.

## Decision

Atlas will target **at‑least‑once execution semantics** for tasks.

### Rationale

- Simpler implementation: retries can safely re‑run a task without needing distributed transaction protocols.
- Allows the platform to focus on reliability and fault‑tolerance first.
- Provides a clear contract to downstream developers: they must design tasks to be safe for repeated execution.

### Trade‑offs

- Duplicate executions may occur, especially when a worker crashes after completing a task but before the result is recorded.
- Users must implement idempotent logic or use external deduplication mechanisms.

## Consequences

- Documentation will emphasize idempotency guidelines (see `docs/design/idempotency.md`).
- The system will record an **execution attempt ID** for each attempt; this can be used by developers to detect duplicates.
- Future phases may introduce optional exactly‑once guarantees for specific workloads.
