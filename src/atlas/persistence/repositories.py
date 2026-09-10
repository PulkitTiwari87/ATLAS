"""Repositories mapping atlas.domain objects to/from PostgreSQL rows.

Each repository takes an explicit SQLAlchemy Session (see atlas.persistence.db
.session_scope) so transaction boundaries stay with the caller.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from atlas.domain import Job, JobStatus, Task, TaskAttempt, TaskStatus, Worker, WorkerStatus
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow


def _job_to_domain(row: JobRow) -> Job:
    return Job(
        name=row.name,
        priority=row.priority,
        id=row.id,
        status=JobStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _job_to_row(job: Job) -> JobRow:
    return JobRow(
        id=job.id,
        name=job.name,
        status=job.status.value,
        priority=job.priority,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


class JobRepository:
    def add(self, session: Session, job: Job) -> Job:
        session.add(_job_to_row(job))
        session.flush()
        return job

    def get(self, session: Session, job_id: uuid.UUID) -> Optional[Job]:
        row = session.get(JobRow, job_id)
        return _job_to_domain(row) if row else None

    def update(self, session: Session, job: Job) -> Job:
        row = session.get(JobRow, job.id)
        row.name = job.name
        row.status = job.status.value
        row.priority = job.priority
        row.updated_at = job.updated_at
        session.flush()
        return job

    def list_by_status(self, session: Session, status: JobStatus) -> List[Job]:
        rows = session.scalars(select(JobRow).where(JobRow.status == status.value))
        return [_job_to_domain(row) for row in rows]

    def list_all(self, session: Session) -> List[Job]:
        rows = session.scalars(select(JobRow))
        return [_job_to_domain(row) for row in rows]


def _task_to_domain(row: TaskRow) -> Task:
    return Task(
        job_id=row.job_id,
        type=row.type,
        payload=row.payload,
        max_retries=row.max_retries,
        id=row.id,
        status=TaskStatus(row.status),
        attempt=row.attempt,
        worker_id=row.worker_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _task_to_row(task: Task) -> TaskRow:
    return TaskRow(
        id=task.id,
        job_id=task.job_id,
        type=task.type,
        payload=task.payload,
        status=task.status.value,
        attempt=task.attempt,
        max_retries=task.max_retries,
        worker_id=task.worker_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


class TaskRepository:
    def add(self, session: Session, task: Task) -> Task:
        session.add(_task_to_row(task))
        session.flush()
        return task

    def get(self, session: Session, task_id: uuid.UUID) -> Optional[Task]:
        row = session.get(TaskRow, task_id)
        return _task_to_domain(row) if row else None

    def update(self, session: Session, task: Task) -> Task:
        row = session.get(TaskRow, task.id)
        row.type = task.type
        row.payload = task.payload
        row.status = task.status.value
        row.attempt = task.attempt
        row.max_retries = task.max_retries
        row.worker_id = task.worker_id
        row.updated_at = task.updated_at
        session.flush()
        return task

    def list_by_job(self, session: Session, job_id: uuid.UUID) -> List[Task]:
        rows = session.scalars(select(TaskRow).where(TaskRow.job_id == job_id))
        return [_task_to_domain(row) for row in rows]

    def list_by_status(self, session: Session, status: TaskStatus) -> List[Task]:
        rows = session.scalars(select(TaskRow).where(TaskRow.status == status.value))
        return [_task_to_domain(row) for row in rows]

    def list_by_worker(self, session: Session, worker_id: uuid.UUID) -> List[Task]:
        rows = session.scalars(select(TaskRow).where(TaskRow.worker_id == worker_id))
        return [_task_to_domain(row) for row in rows]


def _worker_to_domain(row: WorkerRow) -> Worker:
    return Worker(
        hostname=row.hostname,
        address=row.address,
        id=row.id,
        status=WorkerStatus(row.status),
        last_heartbeat=row.last_heartbeat,
        created_at=row.created_at,
    )


def _worker_to_row(worker: Worker) -> WorkerRow:
    return WorkerRow(
        id=worker.id,
        hostname=worker.hostname,
        address=worker.address,
        status=worker.status.value,
        last_heartbeat=worker.last_heartbeat,
        created_at=worker.created_at,
    )


class WorkerRepository:
    def add(self, session: Session, worker: Worker) -> Worker:
        session.add(_worker_to_row(worker))
        session.flush()
        return worker

    def get(self, session: Session, worker_id: uuid.UUID) -> Optional[Worker]:
        row = session.get(WorkerRow, worker_id)
        return _worker_to_domain(row) if row else None

    def update(self, session: Session, worker: Worker) -> Worker:
        row = session.get(WorkerRow, worker.id)
        row.hostname = worker.hostname
        row.address = worker.address
        row.status = worker.status.value
        row.last_heartbeat = worker.last_heartbeat
        session.flush()
        return worker

    def list_by_status(self, session: Session, status: WorkerStatus) -> List[Worker]:
        rows = session.scalars(select(WorkerRow).where(WorkerRow.status == status.value))
        return [_worker_to_domain(row) for row in rows]

    def update_heartbeat(self, session: Session, worker_id: uuid.UUID, timestamp: datetime) -> None:
        """Narrow, single-column write — see WorkerRuntime's heartbeat thread.

        Deliberately does NOT go through update()'s full-row write: a
        concurrent execute_task() call may be committing a status change
        (AVAILABLE<->BUSY) at the same moment, and a full-row heartbeat
        write could silently revert it using a stale in-memory status.
        """
        session.execute(
            update(WorkerRow).where(WorkerRow.id == worker_id).values(last_heartbeat=timestamp)
        )


def _task_attempt_to_row(attempt: TaskAttempt) -> TaskAttemptRow:
    return TaskAttemptRow(
        id=attempt.id,
        task_id=attempt.task_id,
        worker_id=attempt.worker_id,
        attempt_number=attempt.attempt_number,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
        result=attempt.result,
        error=attempt.error,
    )


class TaskAttemptRepository:
    def add(self, session: Session, attempt: TaskAttempt) -> TaskAttempt:
        session.add(_task_attempt_to_row(attempt))
        session.flush()
        return attempt
