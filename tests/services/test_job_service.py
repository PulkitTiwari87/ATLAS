"""JobService tests against the real PostgreSQL service (skips if unreachable)."""

import uuid

import pytest
from sqlalchemy import text

from atlas.domain import InvalidTransitionError, JobStatus, TaskStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow
from atlas.services import JobNotFoundError, JobService


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


def _cleanup(job_id):
    with session_scope() as session:
        for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
            session.delete(row)
        job_row = session.get(JobRow, job_id)
        if job_row:
            session.delete(job_row)


def test_create_and_get_job():
    service = JobService()
    job, tasks = service.create_job(
        name="svc-job", priority=2, tasks=[("shell", {"command": "echo hi"})]
    )
    try:
        assert job.status == JobStatus.SUBMITTED
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.PENDING

        fetched_job, fetched_tasks = service.get_job(job.id)
        assert fetched_job == job
        assert [t.id for t in fetched_tasks] == [tasks[0].id]
    finally:
        _cleanup(job.id)


def test_get_job_missing_returns_none():
    service = JobService()

    assert service.get_job(uuid.uuid4()) is None


def test_list_jobs_filters_by_status():
    service = JobService()
    job, _ = service.create_job(name="svc-list-job", priority=0, tasks=[("shell", {})])
    try:
        submitted = service.list_jobs(status=JobStatus.SUBMITTED)
        assert job.id in [j.id for j in submitted]

        running = service.list_jobs(status=JobStatus.RUNNING)
        assert job.id not in [j.id for j in running]

        all_jobs = service.list_jobs()
        assert job.id in [j.id for j in all_jobs]
    finally:
        _cleanup(job.id)


def test_cancel_job_cancels_pending_tasks():
    service = JobService()
    job, tasks = service.create_job(
        name="svc-cancel-job", priority=0, tasks=[("shell", {})]
    )
    try:
        cancelled = service.cancel_job(job.id)
        assert cancelled.status == JobStatus.CANCELLED

        _, refreshed_tasks = service.get_job(job.id)
        assert all(t.status == TaskStatus.CANCELLED for t in refreshed_tasks)
    finally:
        _cleanup(job.id)


def test_cancel_unknown_job_raises_not_found():
    service = JobService()

    with pytest.raises(JobNotFoundError):
        service.cancel_job(uuid.uuid4())


def test_cancel_terminal_job_raises_invalid_transition():
    service = JobService()
    job, _ = service.create_job(name="svc-terminal-job", priority=0, tasks=[("shell", {})])
    try:
        service.cancel_job(job.id)

        with pytest.raises(InvalidTransitionError):
            service.cancel_job(job.id)
    finally:
        _cleanup(job.id)
