# ADR-004 – Heartbeat‑Based Failure Detection

**Status:** Proposed

## Context

Workers must be monitored for liveness so that the control plane can recover tasks from crashed or unresponsive workers. Several mechanisms exist:

1. **Passive polling** – The control plane periodically queries workers.
2. **Active heartbeats** – Workers push periodic heartbeat messages.
3. **External watchdogs** – OS‑level health checks.

## Decision

Atlas adopts **active heartbeat messages** from workers to the control plane.

### Rationale

- Low latency detection of failures.
- Scales well with many workers; each worker sends a small payload at a configurable interval.
- Simpler to implement than bi‑directional health checks.

### Trade‑offs

- Requires workers to reliably schedule and send heartbeats; network partitions could cause false positives.
- Adds a small amount of network traffic.

## Consequences

- The `heartbeat` module will expose an API for workers to post their status.
- Control plane will mark a worker `UNHEALTHY` after missing `2 * HEARTBEAT_INTERVAL` seconds.
- Recovery manager will re‑assign tasks from unhealthy workers.
- Future enhancements may include configurable tolerance, exponential back‑off, and lease‑based approaches.
