"""Scheduler integration tests against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import pytest
from sqlalchemy import text

from atlas.domain import Job, JobStatus, Task, TaskStatus, Worker, WorkerStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository
from atlas.scheduler import Scheduler
from atlas.services import JobService
from atlas.worker import WorkerRuntime


def _postgres_reachable() -> bool:
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(), reason="PostgreSQL is not reachable"
)


def _cleanup(job_id=None, worker_id=None):
    with session_scope() as session:
        if job_id:
            for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
                session.delete(row)
            job_row = session.get(JobRow, job_id)
            if job_row:
                session.delete(job_row)
        if worker_id:
            worker_row = session.get(WorkerRow, worker_id)
            if worker_row:
                session.delete(worker_row)


def _make_job_with_task(priority=0):
    job = Job(name="scheduler-test-job", priority=priority)
    task = Task(job_id=job.id, type="shell", payload={"command": "exit 0"})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)
    return job, task


def test_submitted_job_becomes_queued():
    job, task = _make_job_with_task()
    try:
        Scheduler().run_cycle()

        with session_scope() as session:
            fetched = JobRepository().get(session, job.id)
        assert fetched.status == JobStatus.QUEUED
    finally:
        _cleanup(job_id=job.id)


def test_already_queued_job_is_not_reprocessed():
    job, task = _make_job_with_task()
    try:
        scheduler = Scheduler()
        scheduler.run_cycle()
        scheduler.run_cycle()  # second cycle must not raise or change anything

        with session_scope() as session:
            fetched = JobRepository().get(session, job.id)
        assert fetched.status == JobStatus.QUEUED
    finally:
        _cleanup(job_id=job.id)


def test_terminal_job_is_ignored():
    job, task = _make_job_with_task()
    try:
        with session_scope() as session:
            fetched_job = JobRepository().get(session, job.id)
            fetched_job.transition_to(JobStatus.CANCELLED)
            JobRepository().update(session, fetched_job)

            fetched_task = TaskRepository().get(session, task.id)
            fetched_task.transition_to(TaskStatus.CANCELLED)
            TaskRepository().update(session, fetched_task)

        Scheduler().run_cycle()

        with session_scope() as session:
            fetched = JobRepository().get(session, job.id)
        assert fetched.status == JobStatus.CANCELLED
    finally:
        _cleanup(job_id=job.id)


def test_priority_ordering_is_respected():
    low_job, low_task = _make_job_with_task(priority=1)
    high_job, high_task = _make_job_with_task(priority=10)
    try:
        worker = Worker(hostname="h", address="h:1", status=WorkerStatus.AVAILABLE)
        with session_scope() as session:
            WorkerRepository().add(session, worker)

        proposals = Scheduler().run_cycle()
        proposed_task_ids = [task.id for task, _ in proposals]

        assert proposed_task_ids[0] == high_task.id
    finally:
        _cleanup(job_id=low_job.id)
        _cleanup(job_id=high_job.id, worker_id=worker.id)


def test_run_cycle_does_not_mutate_task_or_worker_state():
    job, task = _make_job_with_task()
    worker = Worker(hostname="h", address="h:1", status=WorkerStatus.AVAILABLE)
    with session_scope() as session:
        WorkerRepository().add(session, worker)
    try:
        Scheduler().run_cycle()

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
            fetched_worker = WorkerRepository().get(session, worker.id)

        assert fetched_task.status == TaskStatus.PENDING
        assert fetched_task.worker_id is None
        assert fetched_worker.status == WorkerStatus.AVAILABLE
    finally:
        _cleanup(job_id=job.id, worker_id=worker.id)


def test_no_available_workers_still_reports_proposals_empty_but_advances_job():
    job, task = _make_job_with_task()
    try:
        proposals = Scheduler().run_cycle()

        assert proposals == []

        with session_scope() as session:
            fetched_job = JobRepository().get(session, job.id)
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_job.status == JobStatus.QUEUED
        assert fetched_task.status == TaskStatus.PENDING
    finally:
        _cleanup(job_id=job.id)


def test_cancelled_job_tasks_never_proposed():
    job = Job(name="cancel-before-schedule")
    task = Task(job_id=job.id, type="shell", payload={"command": "exit 0"})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)
    try:
        JobService().cancel_job(job.id)

        worker = Worker(hostname="h", address="h:1", status=WorkerStatus.AVAILABLE)
        with session_scope() as session:
            WorkerRepository().add(session, worker)

        proposals = Scheduler().run_cycle()

        assert task.id not in [t.id for t, _ in proposals]
    finally:
        _cleanup(job_id=job.id, worker_id=worker.id)


def test_execute_task_still_works_after_scheduler_cycle():
    job, task = _make_job_with_task()
    runtime = WorkerRuntime()
    runtime.register()
    try:
        Scheduler().run_cycle()

        result = runtime.execute_task(task.id)

        assert result.status == TaskStatus.SUCCEEDED
    finally:
        _cleanup(job_id=job.id, worker_id=runtime.worker.id)
