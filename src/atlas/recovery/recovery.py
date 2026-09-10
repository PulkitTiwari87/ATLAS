"""RecoveryManager: recovers tasks orphaned by an UNHEALTHY worker and
applies retry policy, then retires the worker to DEAD.

Consumes Phase 08's UNHEALTHY workers. Never touches a worker's tasks
before Phase 08 has already marked it UNHEALTHY, and never claims/executes
a task itself — see plans/phase-09-recovery-retries.md.
"""

import logging
import time
from typing import List
from uuid import UUID

from atlas.domain import InvalidTransitionError, Task, TaskStatus, Worker, WorkerStatus
from atlas.observability import log_event
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import TaskRepository, WorkerRepository

logger = logging.getLogger("atlas.recovery")

_ORPHANED_TASK_STATUSES = (TaskStatus.RUNNING, TaskStatus.ASSIGNED)


class RecoveryManager:
    def __init__(self):
        self._workers = WorkerRepository()
        self._tasks = TaskRepository()
        self._stopping = False

    def run_cycle(self) -> None:
        for worker in self._load_unhealthy_workers():
            self._recover_worker(worker.id)

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                self.run_cycle()
            except Exception:
                logger.exception("Recovery cycle failed")
            if self._stopping:
                break
            time.sleep(poll_interval_seconds)

    def _load_unhealthy_workers(self) -> List[Worker]:
        with session_scope() as session:
            return self._workers.list_by_status(session, WorkerStatus.UNHEALTHY)

    def _recover_worker(self, worker_id: UUID) -> None:
        for task in self._load_orphaned_tasks(worker_id):
            self._recover_task(task.id)
        self._retire_worker(worker_id)

    def _load_orphaned_tasks(self, worker_id: UUID) -> List[Task]:
        with session_scope() as session:
            tasks = self._tasks.list_by_worker(session, worker_id)
        return [t for t in tasks if t.status in _ORPHANED_TASK_STATUSES]

    def _recover_task(self, task_id: UUID) -> None:
        with session_scope() as session:
            task = self._tasks.get(session, task_id)
            if task is None or task.status not in _ORPHANED_TASK_STATUSES:
                return

            try:
                task.transition_to(TaskStatus.FAILED)
            except InvalidTransitionError:
                logger.info("Task %s no longer recoverable", task_id)
                return

            retried = task.attempt < task.max_retries
            if retried:
                task.transition_to(TaskStatus.RETRYING)
                task.transition_to(TaskStatus.PENDING)
                task.worker_id = None

            self._tasks.update(session, task)
        log_event(
            logger,
            "task.recovered",
            task_id=task_id,
            attempt=task.attempt,
            max_retries=task.max_retries,
            outcome="retried" if retried else "terminal_failed",
        )

    def _retire_worker(self, worker_id: UUID) -> None:
        with session_scope() as session:
            worker = self._workers.get(session, worker_id)
            if worker is None or worker.status != WorkerStatus.UNHEALTHY:
                return
            try:
                worker.transition_to(WorkerStatus.DEAD)
            except InvalidTransitionError:
                logger.info("Worker %s no longer eligible for DEAD", worker_id)
                return
            self._workers.update(session, worker)
        log_event(logger, "worker.dead", worker_id=worker_id)
