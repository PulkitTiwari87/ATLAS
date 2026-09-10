"""Dispatch integration tests against real PostgreSQL + real WorkerRuntime.

Skipped automatically when PostgreSQL is not reachable.
"""

import pytest
from sqlalchemy import text

from atlas.domain import InvalidTransitionError, Job, JobStatus, Task, TaskStatus, WorkerStatus
from atlas.dispatch import DispatchLoop, Dispatcher, WorkerRegistry
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository
from atlas.scheduler import Scheduler
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


def _make_job_and_task(task_type="shell", payload=None, priority=0):
    job = Job(name="dispatch-test-job", priority=priority)
    task = Task(job_id=job.id, type=task_type, payload=payload or {})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)
    return job, task


def _cleanup(job_id=None, worker_ids=None):
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
        for worker_id in worker_ids or []:
            worker_row = session.get(WorkerRow, worker_id)
            if worker_row:
                session.delete(worker_row)


def test_end_to_end_successful_dispatch():
    job, task = _make_job_and_task(payload={"command": "exit 0"})
    runtime = WorkerRuntime()
    runtime.register()
    registry = WorkerRegistry()
    registry.register(runtime)
    dispatcher = Dispatcher(registry)
    scheduler = Scheduler()
    try:
        proposals = scheduler.run_cycle()
        results = dispatcher.dispatch_all(proposals)

        assert any(r.status == "dispatched" for r in results)

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
            fetched_job = JobRepository().get(session, job.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
        assert fetched_job.status == JobStatus.QUEUED
        assert runtime.worker.status == WorkerStatus.AVAILABLE

        with session_scope() as session:
            attempts = list(
                session.query(TaskAttemptRow).filter(TaskAttemptRow.task_id == task.id)
            )
        assert len(attempts) == 1
        assert attempts[0].error is None
    finally:
        _cleanup(job.id, [runtime.worker.id])


def test_end_to_end_failed_dispatch():
    job, task = _make_job_and_task(payload={"command": "exit 7"})
    runtime = WorkerRuntime()
    runtime.register()
    registry = WorkerRegistry()
    registry.register(runtime)
    dispatcher = Dispatcher(registry)
    scheduler = Scheduler()
    try:
        proposals = scheduler.run_cycle()
        dispatcher.dispatch_all(proposals)

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.FAILED
        assert runtime.worker.status == WorkerStatus.AVAILABLE

        with session_scope() as session:
            attempts = list(
                session.query(TaskAttemptRow).filter(TaskAttemptRow.task_id == task.id)
            )
        assert attempts[0].error is not None
    finally:
        _cleanup(job.id, [runtime.worker.id])


def test_stale_proposal_already_claimed_is_skipped_safely():
    job, task = _make_job_and_task(payload={"command": "exit 0"})
    runtime_a = WorkerRuntime()
    runtime_b = WorkerRuntime()
    runtime_a.register()
    runtime_b.register()
    registry = WorkerRegistry()
    registry.register(runtime_a)
    registry.register(runtime_b)
    dispatcher = Dispatcher(registry)
    try:
        runtime_a.execute_task(task.id)

        result = dispatcher.dispatch(task, runtime_b.worker)

        assert result.status == "skipped"

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
    finally:
        _cleanup(job.id, [runtime_a.worker.id, runtime_b.worker.id])


def test_worker_not_in_registry_leaves_task_untouched():
    job, task = _make_job_and_task(payload={"command": "exit 0"})
    runtime = WorkerRuntime()
    runtime.register()
    registry = WorkerRegistry()  # deliberately not registered
    dispatcher = Dispatcher(registry)
    try:
        scheduler = Scheduler()
        proposals = scheduler.run_cycle()

        results = dispatcher.dispatch_all(proposals)

        assert all(r.status == "skipped" for r in results)

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.PENDING
    finally:
        _cleanup(job.id, [runtime.worker.id])


def test_two_proposals_two_workers_both_succeed():
    job_a, task_a = _make_job_and_task(payload={"command": "exit 0"})
    job_b, task_b = _make_job_and_task(payload={"command": "exit 0"})
    runtime_a = WorkerRuntime()
    runtime_b = WorkerRuntime()
    runtime_a.register()
    runtime_b.register()
    registry = WorkerRegistry()
    registry.register(runtime_a)
    registry.register(runtime_b)
    dispatcher = Dispatcher(registry)
    scheduler = Scheduler()
    try:
        proposals = scheduler.run_cycle()
        assert len(proposals) == 2

        results = dispatcher.dispatch_all(proposals)

        assert all(r.status == "dispatched" for r in results)
        with session_scope() as session:
            fetched_a = TaskRepository().get(session, task_a.id)
            fetched_b = TaskRepository().get(session, task_b.id)
        assert fetched_a.status == TaskStatus.SUCCEEDED
        assert fetched_b.status == TaskStatus.SUCCEEDED
    finally:
        _cleanup(job_a.id, [runtime_a.worker.id])
        _cleanup(job_b.id, [runtime_b.worker.id])


def test_dispatch_loop_end_to_end():
    job, task = _make_job_and_task(payload={"command": "exit 0"})
    runtime = WorkerRuntime()
    runtime.register()
    registry = WorkerRegistry()
    registry.register(runtime)
    loop = DispatchLoop(Scheduler(), Dispatcher(registry))
    try:
        loop.run_cycle()

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
    finally:
        _cleanup(job.id, [runtime.worker.id])


def test_dispatch_never_touches_state_directly_only_via_execute_task():
    """Regression for section 12: Dispatcher opens no session of its own,
    so a dispatch that never reaches execute_task (unregistered worker)
    cannot have changed anything."""
    job, task = _make_job_and_task(payload={"command": "exit 0"})
    registry = WorkerRegistry()
    dispatcher = Dispatcher(registry)
    from atlas.domain import Worker

    fake_worker = Worker(hostname="ghost", address="ghost:0")
    try:
        result = dispatcher.dispatch(task, fake_worker)

        assert result.status == "skipped"
        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.PENDING
        assert fetched_task.worker_id is None
    finally:
        _cleanup(job.id)
