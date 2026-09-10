"""Heartbeat integration tests against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import threading
import time

import pytest
from sqlalchemy import text

from atlas.domain import Job, Task, TaskStatus, WorkerStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow, WorkerRow
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


def _last_heartbeat(worker_id):
    with session_scope() as session:
        row = session.get(WorkerRow, worker_id)
        return row.last_heartbeat if row else None


def test_registration_starts_heartbeat_and_persists_it():
    runtime = WorkerRuntime()
    try:
        runtime.register()

        # register() only schedules the heartbeat thread; it doesn't block
        # until the first tick lands. Poll with a bound instead of a single
        # immediate read, which is inherently racy under load.
        first = None
        deadline = time.monotonic() + 2
        while first is None and time.monotonic() < deadline:
            first = _last_heartbeat(runtime.worker.id)
            if first is None:
                time.sleep(0.01)
        assert first is not None

        runtime.send_heartbeat()
        second = _last_heartbeat(runtime.worker.id)
        assert second is not None
        assert second >= first
    finally:
        runtime.stop_heartbeat()
        _cleanup(worker_id=runtime.worker.id)


def test_repeated_heartbeat_ticks_are_persisted():
    runtime = WorkerRuntime()
    try:
        runtime.register()
        runtime.stop_heartbeat()
        runtime.start_heartbeat(interval_seconds=0.05)
        time.sleep(0.01)
        first = _last_heartbeat(runtime.worker.id)
        time.sleep(0.2)
        second = _last_heartbeat(runtime.worker.id)

        assert second > first
    finally:
        runtime.stop_heartbeat()
        _cleanup(worker_id=runtime.worker.id)


def test_shutdown_stops_heartbeat():
    runtime = WorkerRuntime()
    try:
        runtime.register()
        runtime.shutdown()

        assert _last_heartbeat(runtime.worker.id) is not None
        last_before_wait = _last_heartbeat(runtime.worker.id)
        time.sleep(0.2)
        last_after_wait = _last_heartbeat(runtime.worker.id)

        assert last_after_wait == last_before_wait
        assert runtime.worker.status == WorkerStatus.DEAD
    finally:
        _cleanup(worker_id=runtime.worker.id)


def test_worker_and_task_state_remain_correct_with_heartbeat_running():
    job = Job(name="heartbeat-test-job")
    task = Task(job_id=job.id, type="shell", payload={"command": "exit 0"})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)

    runtime = WorkerRuntime()
    try:
        runtime.register()
        result = runtime.execute_task(task.id)

        assert result.status == TaskStatus.SUCCEEDED
        assert runtime.worker.status == WorkerStatus.AVAILABLE
    finally:
        runtime.stop_heartbeat()
        _cleanup(job.id, runtime.worker.id)


def test_heartbeat_does_not_clobber_concurrent_available_to_busy():
    """Regression: heartbeat's narrow update must not revert a status
    change execute_task() commits concurrently (the known full-row race)."""
    job = Job(name="heartbeat-race-job")
    task = Task(job_id=job.id, type="shell", payload={"command": "sleep 0.3"})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)

    runtime = WorkerRuntime()
    try:
        runtime.register()
        runtime.start_heartbeat(interval_seconds=0.02)

        result_holder = {}

        def _run():
            result_holder["task"] = runtime.execute_task(task.id)

        exec_thread = threading.Thread(target=_run)
        exec_thread.start()
        exec_thread.join(timeout=5)

        assert result_holder["task"].status == TaskStatus.SUCCEEDED

        with session_scope() as session:
            row = session.get(WorkerRow, runtime.worker.id)
        assert row.status == WorkerStatus.AVAILABLE.value
        assert runtime.worker.status == WorkerStatus.AVAILABLE
    finally:
        runtime.stop_heartbeat()
        _cleanup(job.id, runtime.worker.id)


def test_heartbeat_does_not_clobber_busy_to_available_either():
    """Same regression, focused on the BUSY -> AVAILABLE edge specifically."""
    job = Job(name="heartbeat-race-job-2")
    task = Task(job_id=job.id, type="shell", payload={"command": "sleep 0.2"})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)

    runtime = WorkerRuntime()
    try:
        runtime.register()
        runtime.start_heartbeat(interval_seconds=0.01)

        runtime.execute_task(task.id)
        time.sleep(0.1)  # let a few more heartbeat ticks land after _finish()

        with session_scope() as session:
            row = session.get(WorkerRow, runtime.worker.id)
        assert row.status == WorkerStatus.AVAILABLE.value
    finally:
        runtime.stop_heartbeat()
        _cleanup(job.id, runtime.worker.id)
