# Scheduling

Atlas uses a priority queue to determine which task should be executed next.

- **Priority** – Integer value; higher numbers indicate higher priority.
- **Worker Availability** – Scheduler checks the registry for workers in `AVAILABLE` state.
- **Fairness** – Simple FIFO within the same priority level.
- **Starvation** – Not addressed in the initial implementation; may be revisited later.

### Scheduler Loop (Pseudo‑code)

```python
while True:
    task = queue.pop_highest_priority()
    worker = registry.get_available_worker()
    if not worker:
        queue.requeue(task)
        sleep(1)
        continue
    dispatcher.assign(task, worker)
```
