"""Integration tests against the real PostgreSQL service (Phase 00 Docker Compose).

Skipped automatically when PostgreSQL is not reachable.
"""

import pytest
from sqlalchemy import text

from atlas.domain import Job, JobStatus, Task, Worker, WorkerStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository


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


def test_job_repository_add_get_update():
    repo = JobRepository()
    job = Job(name="test-job", priority=1)

    with session_scope() as session:
        repo.add(session, job)

    with session_scope() as session:
        fetched = repo.get(session, job.id)
        assert fetched == job

        fetched.transition_to(JobStatus.QUEUED)
        repo.update(session, fetched)

    with session_scope() as session:
        updated = repo.get(session, job.id)
        assert updated.status == JobStatus.QUEUED

        session.delete(session.get(JobRow, job.id))


def test_worker_repository_add_get_update():
    repo = WorkerRepository()
    worker = Worker(hostname="host-x", address="10.0.0.9:9000")

    with session_scope() as session:
        repo.add(session, worker)

    with session_scope() as session:
        fetched = repo.get(session, worker.id)
        assert fetched == worker

        fetched.transition_to(WorkerStatus.AVAILABLE)
        repo.update(session, fetched)

    with session_scope() as session:
        updated = repo.get(session, worker.id)
        assert updated.status == WorkerStatus.AVAILABLE

        session.delete(session.get(WorkerRow, worker.id))


def test_task_repository_add_get_and_list_by_job():
    job_repo = JobRepository()
    task_repo = TaskRepository()
    job = Job(name="job-with-tasks")
    task = Task(job_id=job.id, type="shell", payload={"cmd": "echo hi"})

    with session_scope() as session:
        job_repo.add(session, job)
        task_repo.add(session, task)

    with session_scope() as session:
        fetched = task_repo.get(session, task.id)
        assert fetched == task

        tasks = task_repo.list_by_job(session, job.id)
        assert [t.id for t in tasks] == [task.id]

        session.delete(session.get(TaskRow, task.id))
        session.delete(session.get(JobRow, job.id))


def test_deleting_job_cascades_to_its_tasks():
    job_repo = JobRepository()
    task_repo = TaskRepository()
    job = Job(name="cascade-job")
    task = Task(job_id=job.id, type="shell")

    with session_scope() as session:
        job_repo.add(session, job)
        task_repo.add(session, task)

    with session_scope() as session:
        session.delete(session.get(JobRow, job.id))

    with session_scope() as session:
        assert task_repo.get(session, task.id) is None


def test_transaction_rolls_back_on_error():
    job = Job(name="rollback-job")

    with pytest.raises(RuntimeError):
        with session_scope() as session:
            JobRepository().add(session, job)
            raise RuntimeError("boom")

    with session_scope() as session:
        assert JobRepository().get(session, job.id) is None
