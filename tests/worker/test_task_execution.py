"""WorkerRuntime task-execution integration tests against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import uuid

import pytest
from sqlalchemy import text

from atlas.domain import InvalidTransitionError, Job, Task, TaskStatus, WorkerStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository
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


def _make_job_and_task(task_type: str, payload: dict) -> Task:
    job = Job(name="worker-test-job")
    task = Task(job_id=job.id, type=task_type, payload=payload)
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)
    return task


def _attempts_for(task_id) -> list:
    with session_scope() as session:
        return list(session.query(TaskAttemptRow).filter(TaskAttemptRow.task_id == task_id))


def _cleanup(job_id=None, worker_id=None):
    with session_scope() as session:
        if job_id:
            for row in session.query(TaskAttemptRow).join(
                TaskRow, TaskAttemptRow.task_id == TaskRow.id
            ).filter(TaskRow.job_id == job_id):
                session.delete(row)
            for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
                session.delete(row)
            job_row = session.get(JobRow, job_id)
            if job_row:
                session.delete(job_row)
        if worker_id:
            worker_row = session.get(WorkerRow, worker_id)
            if worker_row:
                session.delete(worker_row)


def test_register_persists_worker_row():
    runtime = WorkerRuntime()
    runtime.register()
    try:
        with session_scope() as session:
            row = session.get(WorkerRow, runtime.worker.id)
            assert row is not None
            assert row.status == WorkerStatus.AVAILABLE.value
    finally:
        _cleanup(worker_id=runtime.worker.id)


def test_successful_shell_task():
    task = _make_job_and_task("shell", {"command": "exit 0"})
    runtime = WorkerRuntime()
    runtime.register()
    try:
        result_task = runtime.execute_task(task.id)

        assert result_task.status == TaskStatus.SUCCEEDED
        assert runtime.worker.status == WorkerStatus.AVAILABLE

        attempts = _attempts_for(task.id)
        assert len(attempts) == 1
        assert attempts[0].error is None
        assert attempts[0].result is not None
    finally:
        _cleanup(task.job_id, runtime.worker.id)


def test_failed_shell_task_nonzero_exit():
    task = _make_job_and_task("shell", {"command": "exit 7"})
    runtime = WorkerRuntime()
    runtime.register()
    try:
        result_task = runtime.execute_task(task.id)

        assert result_task.status == TaskStatus.FAILED
        assert runtime.worker.status == WorkerStatus.AVAILABLE

        attempts = _attempts_for(task.id)
        assert len(attempts) == 1
        assert attempts[0].error is not None
    finally:
        _cleanup(task.job_id, runtime.worker.id)


def test_unknown_task_type_fails_cleanly():
    task = _make_job_and_task("no-such-type", {})
    runtime = WorkerRuntime()
    runtime.register()
    try:
        result_task = runtime.execute_task(task.id)

        assert result_task.status == TaskStatus.FAILED
        assert runtime.worker.status == WorkerStatus.AVAILABLE
    finally:
        _cleanup(task.job_id, runtime.worker.id)


def test_double_assignment_raises_invalid_transition():
    task = _make_job_and_task("shell", {"command": "exit 0"})
    runtime_a = WorkerRuntime()
    runtime_b = WorkerRuntime()
    runtime_a.register()
    runtime_b.register()
    try:
        with session_scope() as session:
            fetched = TaskRepository().get(session, task.id)
            fetched.transition_to(TaskStatus.ASSIGNED)
            TaskRepository().update(session, fetched)

        with pytest.raises(InvalidTransitionError):
            runtime_b.execute_task(task.id)
    finally:
        _cleanup(task.job_id, runtime_a.worker.id)
        with session_scope() as session:
            row = session.get(WorkerRow, runtime_b.worker.id)
            if row:
                session.delete(row)
