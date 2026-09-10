# Idempotency

Because Atlas provides at‑least‑once execution semantics, tasks may be executed more than once. Implementations should therefore make task logic idempotent or use mechanisms such as:

- **Execution IDs** – Unique identifier for each attempt; workers can detect duplicate attempts.
- **Transactional Updates** – Only commit results if the task has not already been marked completed.
- **Deduplication** – Control plane can check for previous successful execution before processing a new attempt.

Guidelines for developers:
1. Design task payloads to be safe for repeated execution.
2. Use deterministic operations where possible.
3. Record side‑effects in a way that can be checked before re‑applying.

Future work may include explicit idempotency keys in the API.
