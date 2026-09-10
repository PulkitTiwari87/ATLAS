# Retries

Atlas supports configurable retry policies for tasks.

- **max_retries** – Maximum number of retry attempts (default 3).
- **retry_count** – Incremented each time a task fails and is re‑queued.
- **Transient vs Permanent Failure** – Currently all failures trigger a retry until the limit is reached; future work may classify errors.
- **Backoff** – Not implemented initially; tasks are re‑queued immediately after failure detection.

When `retry_count` exceeds `max_retries`, the task transitions to **FAILED**.
