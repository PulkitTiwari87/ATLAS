"""Job lifecycle business logic. Routes call this; this calls repositories."""

import logging
import uuid
from typing import List, Optional, Tuple

from atlas.domain import Job, JobStatus, Task, TaskStatus
from atlas.observability import log_event
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import JobRepository, TaskRepository
from atlas.services.errors import JobNotFoundError

logger = logging.getLogger("atlas.services.job")

_NON_TERMINAL_TASK_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.ASSIGNED,
    TaskStatus.RUNNING,
}


class JobService:
    def __init__(self):
        self._jobs = JobRepository()
        self._tasks = TaskRepository()

    def create_job(
        self, name: str, priority: int, tasks: List[Tuple[str, dict]]
    ) -> Tuple[Job, List[Task]]:
        job = Job(name=name, priority=priority)
        task_objects = [
            Task(job_id=job.id, type=task_type, payload=payload)
            for task_type, payload in tasks
        ]

        with session_scope() as session:
            self._jobs.add(session, job)
            for task in task_objects:
                self._tasks.add(session, task)

        log_event(
            logger,
            "job.created",
            job_id=job.id,
            name=job.name,
            priority=job.priority,
            task_count=len(task_objects),
        )
        return job, task_objects

    def get_job(self, job_id: uuid.UUID) -> Optional[Tuple[Job, List[Task]]]:
        with session_scope() as session:
            job = self._jobs.get(session, job_id)
            if job is None:
                return None
            tasks = self._tasks.list_by_job(session, job_id)
            return job, tasks

    def list_jobs(self, status: Optional[JobStatus] = None) -> List[Job]:
        with session_scope() as session:
            if status is not None:
                return self._jobs.list_by_status(session, status)
            return self._jobs.list_all(session)

    def cancel_job(self, job_id: uuid.UUID) -> Job:
        with session_scope() as session:
            job = self._jobs.get(session, job_id)
            if job is None:
                raise JobNotFoundError(job_id)

            job.transition_to(JobStatus.CANCELLED)
            self._jobs.update(session, job)

            for task in self._tasks.list_by_job(session, job_id):
                if task.status in _NON_TERMINAL_TASK_STATUSES:
                    task.transition_to(TaskStatus.CANCELLED)
                    self._tasks.update(session, task)

            log_event(logger, "job.cancelled", job_id=job.id)
            return job
