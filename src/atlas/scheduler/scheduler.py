"""Scheduler: decides which runnable tasks should run next, on which worker.

Read-only for Task/Worker state — the only persisted write is the one-time
Job SUBMITTED -> QUEUED move (see plans/phase-05-scheduler.md section 14).
The actual PENDING -> ASSIGNED claim stays inside
atlas.worker.runtime.WorkerRuntime.execute_task() (Phase 04), unchanged.
"""

import logging
import time
from typing import Dict, List, Tuple
from uuid import UUID

from atlas.domain import JobStatus, Task, TaskStatus, Worker, WorkerStatus
from atlas.observability import log_event
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository

logger = logging.getLogger("atlas.scheduler")


class Scheduler:
    def __init__(self):
        self._jobs = JobRepository()
        self._tasks = TaskRepository()
        self._workers = WorkerRepository()
        self._stopping = False

    def run_cycle(self) -> List[Tuple[Task, Worker]]:
        runnable_tasks = self._load_runnable_tasks()
        self._advance_submitted_jobs(runnable_tasks)

        ordered_tasks = self._order_by_priority(runnable_tasks)
        available_workers = self._load_available_workers()

        proposals = list(zip(ordered_tasks, available_workers))
        log_event(
            logger,
            "scheduler.cycle",
            level="debug",
            runnable_count=len(runnable_tasks),
            available_workers=len(available_workers),
            proposal_count=len(proposals),
        )
        return proposals

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                self.run_cycle()
            except Exception:
                logger.exception("Scheduler cycle failed")
            if self._stopping:
                break
            time.sleep(poll_interval_seconds)

    def _load_runnable_tasks(self) -> List[Task]:
        with session_scope() as session:
            return self._tasks.list_by_status(session, TaskStatus.PENDING)

    def _load_available_workers(self) -> List[Worker]:
        with session_scope() as session:
            workers = self._workers.list_by_status(session, WorkerStatus.AVAILABLE)
        return sorted(workers, key=lambda w: w.id)

    def _advance_submitted_jobs(self, runnable_tasks: List[Task]) -> None:
        for job_id in {task.job_id for task in runnable_tasks}:
            with session_scope() as session:
                job = self._jobs.get(session, job_id)
                if job is not None and job.status == JobStatus.SUBMITTED:
                    job.transition_to(JobStatus.QUEUED)
                    self._jobs.update(session, job)
                    log_event(logger, "job.queued", job_id=job.id)

    def _order_by_priority(self, tasks: List[Task]) -> List[Task]:
        if not tasks:
            return []
        priorities = self._load_job_priorities({task.job_id for task in tasks})
        return sorted(
            tasks,
            key=lambda task: (-priorities.get(task.job_id, 0), task.created_at),
        )

    def _load_job_priorities(self, job_ids) -> Dict[UUID, int]:
        priorities: Dict[UUID, int] = {}
        with session_scope() as session:
            for job_id in job_ids:
                job = self._jobs.get(session, job_id)
                if job is not None:
                    priorities[job_id] = job.priority
        return priorities
