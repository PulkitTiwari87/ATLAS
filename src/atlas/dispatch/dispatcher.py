"""Dispatcher: turns a Scheduler proposal into a WorkerRuntime.execute_task()
call. Delivers; does not decide (Scheduler) and does not execute (Worker).

Proposals are treated as advice, not a claim (see plans/phase-06-dispatch.md
section 9) — no pre-check query is made. Staleness is caught exclusively via
WorkerRuntime.execute_task()'s existing exceptions, which already guard
against duplicate/stale assignment (InvalidTransitionError) and a draining
worker (WorkerDrainingError).
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from atlas.domain import InvalidTransitionError, Task, Worker
from atlas.dispatch.registry import WorkerRegistry
from atlas.observability import log_event
from atlas.worker.errors import TaskNotFoundError, WorkerDrainingError

logger = logging.getLogger("atlas.dispatch")

_SKIPPABLE_ERRORS = (InvalidTransitionError, WorkerDrainingError, TaskNotFoundError)


@dataclass
class DispatchResult:
    task: Task
    worker: Worker
    status: str  # "dispatched" or "skipped"
    reason: Optional[str] = None


class Dispatcher:
    def __init__(self, registry: WorkerRegistry):
        self._registry = registry

    def dispatch(self, task: Task, worker: Worker) -> DispatchResult:
        runtime = self._registry.get(worker.id)
        if runtime is None:
            log_event(
                logger,
                "dispatch.skipped",
                task_id=task.id,
                worker_id=worker.id,
                reason="worker_not_registered",
            )
            return DispatchResult(task, worker, status="skipped", reason="worker_not_registered")

        try:
            runtime.execute_task(task.id)
        except _SKIPPABLE_ERRORS as exc:
            log_event(
                logger, "dispatch.skipped", task_id=task.id, worker_id=worker.id, reason=str(exc)
            )
            return DispatchResult(task, worker, status="skipped", reason=str(exc))

        log_event(logger, "dispatch.dispatched", task_id=task.id, worker_id=worker.id)
        return DispatchResult(task, worker, status="dispatched")

    def dispatch_all(self, proposals: List[Tuple[Task, Worker]]) -> List[DispatchResult]:
        return [self.dispatch(task, worker) for task, worker in proposals]
