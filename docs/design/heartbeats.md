# Heartbeats

Workers send periodic heartbeat messages to the control plane.

- **interval** – Configurable (default 5 seconds).
- **last_seen** – Timestamp stored in PostgreSQL.
- **timeout** – If `now - last_heartbeat > WORKER_TIMEOUT` (default 30 seconds), worker is considered UNHEALTHY. `WORKER_TIMEOUT` is a dedicated configuration value, independent of `HEARTBEAT_INTERVAL`.

Heartbeat handling is performed by the **Heartbeat Manager** component (`WorkerRuntime`'s heartbeat thread, Phase 07) and interpreted by the **Failure Detector** (Phase 08).
