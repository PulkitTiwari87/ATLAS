"""Scheduler: decides which runnable tasks should run next, on which worker.

Read-only for Task/Worker state. The only persisted writes are the
one-time Job SUBMITTED -> QUEUED move and the job-status aggregation
below (see plans/phase-05-scheduler.md section 14 and
plans/phase-04-worker-runtime.md section 18, which flagged job-status
aggregation as deliberately deferred until an actual multi-task job was
flowing through the system -- it now is). The actual PENDING -> ASSIGNED
claim stays inside atlas.worker.runtime.WorkerRuntime.execute_task()
(Phase 04), unchanged.
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

_SYNCABLE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
_TERMINAL_TASK_STATUSES = {TaskStatus.SUCCEEDED, TaskStatus.FAILED}


class Scheduler:
    def __init__(self):
        self._jobs = JobRepository()
        self._tasks = TaskRepository()
        self._workers = WorkerRepository()
        self._stopping = False

    def run_cycle(self) -> List[Tuple[Task, Worker]]:
        runnable_tasks = self._load_runnable_tasks()
        self._advance_submitted_jobs(runnable_tasks)
        self._sync_job_statuses()

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

    def _sync_job_statuses(self) -> None:
        with session_scope() as session:
            job_ids = [
                job.id
                for status in _SYNCABLE_JOB_STATUSES
                for job in self._jobs.list_by_status(session, status)
            ]
        for job_id in job_ids:
            self._sync_job_status(job_id)

    def _sync_job_status(self, job_id: UUID) -> None:
        # Re-fetches job and tasks fresh rather than reusing run_cycle()'s
        # earlier snapshot -- same staleness guard FailureDetector/Recovery
        # use before a distributed-state transition (see README "Fresh
        # state before critical transitions").
        with session_scope() as session:
            job = self._jobs.get(session, job_id)
            if job is None or job.status not in _SYNCABLE_JOB_STATUSES:
                return
            tasks = self._tasks.list_by_job(session, job_id)
            if not tasks:
                return

            statuses = {task.status for task in tasks}
            all_terminal = statuses <= _TERMINAL_TASK_STATUSES
            started = bool(statuses - {TaskStatus.PENDING})

            if all_terminal:
                outcome = (
                    JobStatus.COMPLETED if statuses <= {TaskStatus.SUCCEEDED} else JobStatus.FAILED
                )
            elif started and job.status == JobStatus.QUEUED:
                outcome = JobStatus.RUNNING
            else:
                return

            if job.status == JobStatus.QUEUED:
                job.transition_to(JobStatus.RUNNING)
            if outcome != JobStatus.RUNNING:
                job.transition_to(outcome)
            self._jobs.update(session, job)
        log_event(logger, f"job.{outcome.value.lower()}", job_id=job_id)

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
