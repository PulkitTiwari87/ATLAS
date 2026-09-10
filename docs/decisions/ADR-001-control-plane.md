# ADR-001 – Control Plane Architecture

**Status:** Proposed

## Context

Atlas must coordinate job scheduling and worker management across a distributed set of nodes. Two broad approaches were considered:

1. **Centralized Control Plane** – A single service owns all scheduling decisions, state persistence, and failure detection.
2. **Fully Decentralized Peer‑to‑Peer** – Workers elect a leader or use a gossip protocol to share state.

## Decision

We adopt a **centralized control plane** for the initial implementation.

### Rationale

- Simpler to reason about state consistency; PostgreSQL provides a single source of truth.
- Enables straightforward implementation of priority scheduling and failure detection.
- Reduces operational complexity for early adopters.
- Allows incremental evolution to a more distributed approach later if needed.

### Trade‑offs

- Single point of failure (mitigated by deploying the control plane behind a load‑balancer and using process supervisors).
- Potential bottleneck at high scale (addressed in later phases with sharding or scaling the control plane).

## Consequences

- All components (API, scheduler, dispatcher, failure detector) run within the control plane service.
- Workers are thin clients that only execute tasks and report heartbeats.
- Future work may introduce HA for the control plane (active‑passive replication).
