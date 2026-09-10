"""RecoveryManager integration tests against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import pytest
from sqlalchemy import text

from atlas.domain import Job, Task, TaskStatus, Worker, WorkerStatus
from atlas.dispatch import Dispatcher, WorkerRegistry
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository
from atlas.recovery import RecoveryManager
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


def _make_orphan(
    task_status,
    attempt=0,
    max_retries=3,
    worker_status=WorkerStatus.UNHEALTHY,
    payload=None,
):
    job = Job(name="recovery-test-job")
    task = Task(
        job_id=job.id,
        type="shell",
        payload=payload if payload is not None else {"command": "exit 0"},
        attempt=attempt,
        max_retries=max_retries,
    )
    worker = Worker(hostname="h", address="h:1", status=worker_status)
    with session_scope() as session:
        JobRepository().add(session, job)
        WorkerRepository().add(session, worker)
        task.worker_id = worker.id
        task.status = task_status
        TaskRepository().add(session, task)
    return job, task, worker


def _fetch_task(task_id):
    with session_scope() as session:
        return TaskRepository().get(session, task_id)


def _fetch_worker(worker_id):
    with session_scope() as session:
        return WorkerRepository().get(session, worker_id)


def test_running_orphan_below_retry_limit_recovers_to_pending():
    job, task, worker = _make_orphan(TaskStatus.RUNNING, attempt=1, max_retries=3)
    try:
        RecoveryManager().run_cycle()

        fetched_task = _fetch_task(task.id)
        assert fetched_task.status == TaskStatus.PENDING
        assert fetched_task.worker_id is None
        assert fetched_task.attempt == 1  # unchanged by recovery

        fetched_worker = _fetch_worker(worker.id)
        assert fetched_worker.status == WorkerStatus.DEAD
    finally:
        _cleanup(job.id, worker.id)


def test_task_at_retry_limit_ends_terminal_failed_worker_still_dead():
    job, task, worker = _make_orphan(TaskStatus.RUNNING, attempt=3, max_retries=3)
    try:
        RecoveryManager().run_cycle()

        fetched_task = _fetch_task(task.id)
        assert fetched_task.status == TaskStatus.FAILED

        fetched_worker = _fetch_worker(worker.id)
        assert fetched_worker.status == WorkerStatus.DEAD
    finally:
        _cleanup(job.id, worker.id)


def test_assigned_orphan_recovers_correctly():
    job, task, worker = _make_orphan(TaskStatus.ASSIGNED, attempt=0, max_retries=3)
    try:
        RecoveryManager().run_cycle()

        fetched_task = _fetch_task(task.id)
        assert fetched_task.status == TaskStatus.PENDING
        assert fetched_task.worker_id is None
    finally:
        _cleanup(job.id, worker.id)


def test_recovered_task_is_rescheduled_and_redispatched_by_unmodified_phase_05_06():
    job, task, worker = _make_orphan(TaskStatus.RUNNING, attempt=0, max_retries=3)
    try:
        RecoveryManager().run_cycle()

        new_worker_runtime = WorkerRuntime()
        new_worker_runtime.register()
        registry = WorkerRegistry()
        registry.register(new_worker_runtime)
        dispatcher = Dispatcher(registry)
        scheduler = Scheduler()

        proposals = scheduler.run_cycle()
        proposed_task_ids = [t.id for t, _ in proposals]
        assert task.id in proposed_task_ids

        results = dispatcher.dispatch_all(proposals)
        assert any(r.status == "dispatched" for r in results)

        fetched_task = _fetch_task(task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
    finally:
        new_worker_runtime.stop_heartbeat()
        _cleanup(job.id, worker.id)
        _cleanup(worker_id=new_worker_runtime.worker.id)


def test_two_overlapping_cycles_do_not_double_recover():
    job, task, worker = _make_orphan(TaskStatus.RUNNING, attempt=0, max_retries=3)
    try:
        manager = RecoveryManager()
        manager.run_cycle()
        manager.run_cycle()  # second cycle must not raise or double-transition

        fetched_task = _fetch_task(task.id)
        assert fetched_task.status == TaskStatus.PENDING

        fetched_worker = _fetch_worker(worker.id)
        assert fetched_worker.status == WorkerStatus.DEAD
    finally:
        _cleanup(job.id, worker.id)


def test_healthy_workers_and_non_orphaned_tasks_are_untouched():
    job = Job(name="healthy-job")
    task = Task(job_id=job.id, type="shell", payload={})
    worker = Worker(hostname="h", address="h:1", status=WorkerStatus.AVAILABLE)
    with session_scope() as session:
        JobRepository().add(session, job)
        WorkerRepository().add(session, worker)
        TaskRepository().add(session, task)
    try:
        RecoveryManager().run_cycle()

        fetched_task = _fetch_task(task.id)
        assert fetched_task.status == TaskStatus.PENDING

        fetched_worker = _fetch_worker(worker.id)
        assert fetched_worker.status == WorkerStatus.AVAILABLE
    finally:
        _cleanup(job.id, worker.id)
