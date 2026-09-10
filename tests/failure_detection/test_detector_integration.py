"""FailureDetector integration tests against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from atlas.domain import Job, Task, TaskStatus, Worker, WorkerStatus
from atlas.failure_detection import FailureDetector
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository
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


def _register_worker(status=WorkerStatus.AVAILABLE, last_heartbeat=None):
    worker = Worker(hostname="h", address="h:1", status=status, last_heartbeat=last_heartbeat)
    with session_scope() as session:
        WorkerRepository().add(session, worker)
    return worker


def _fetch(worker_id):
    with session_scope() as session:
        return WorkerRepository().get(session, worker_id)


def test_stale_available_worker_becomes_unhealthy():
    stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=60)
    worker = _register_worker(status=WorkerStatus.AVAILABLE, last_heartbeat=stale_heartbeat)
    try:
        FailureDetector().run_cycle()

        fetched = _fetch(worker.id)
        assert fetched.status == WorkerStatus.UNHEALTHY
    finally:
        _cleanup(worker_id=worker.id)


def test_fresh_heartbeat_prevents_detection():
    fresh_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=1)
    worker = _register_worker(status=WorkerStatus.AVAILABLE, last_heartbeat=fresh_heartbeat)
    try:
        FailureDetector().run_cycle()

        fetched = _fetch(worker.id)
        assert fetched.status == WorkerStatus.AVAILABLE
    finally:
        _cleanup(worker_id=worker.id)


def test_busy_stale_worker_becomes_unhealthy_task_untouched():
    job = Job(name="fd-test-job")
    task = Task(job_id=job.id, type="shell", payload={})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)

    stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=60)
    worker = _register_worker(status=WorkerStatus.BUSY, last_heartbeat=stale_heartbeat)
    with session_scope() as session:
        fetched_task = TaskRepository().get(session, task.id)
        fetched_task.transition_to(TaskStatus.ASSIGNED)
        fetched_task.worker_id = worker.id
        TaskRepository().update(session, fetched_task)
        fetched_task.transition_to(TaskStatus.RUNNING)
        TaskRepository().update(session, fetched_task)

    try:
        FailureDetector().run_cycle()

        fetched_worker = _fetch(worker.id)
        assert fetched_worker.status == WorkerStatus.UNHEALTHY

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.RUNNING
        assert fetched_task.worker_id == worker.id
    finally:
        _cleanup(job.id, worker.id)


def test_null_heartbeat_worker_becomes_unhealthy():
    worker = _register_worker(status=WorkerStatus.AVAILABLE, last_heartbeat=None)
    try:
        FailureDetector().run_cycle()

        fetched = _fetch(worker.id)
        assert fetched.status == WorkerStatus.UNHEALTHY
    finally:
        _cleanup(worker_id=worker.id)


def test_draining_and_dead_and_unhealthy_workers_are_ignored():
    draining = _register_worker(status=WorkerStatus.DRAINING, last_heartbeat=None)
    dead = _register_worker(status=WorkerStatus.DEAD, last_heartbeat=None)
    unhealthy = _register_worker(status=WorkerStatus.UNHEALTHY, last_heartbeat=None)
    try:
        FailureDetector().run_cycle()

        assert _fetch(draining.id).status == WorkerStatus.DRAINING
        assert _fetch(dead.id).status == WorkerStatus.DEAD
        assert _fetch(unhealthy.id).status == WorkerStatus.UNHEALTHY
    finally:
        _cleanup(worker_id=draining.id)
        _cleanup(worker_id=dead.id)
        _cleanup(worker_id=unhealthy.id)


def test_multiple_workers_only_stale_ones_transition():
    stale = _register_worker(
        status=WorkerStatus.AVAILABLE,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=60),
    )
    fresh = _register_worker(
        status=WorkerStatus.AVAILABLE,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    try:
        FailureDetector().run_cycle()

        assert _fetch(stale.id).status == WorkerStatus.UNHEALTHY
        assert _fetch(fresh.id).status == WorkerStatus.AVAILABLE
    finally:
        _cleanup(worker_id=stale.id)
        _cleanup(worker_id=fresh.id)


def test_concurrent_heartbeat_vs_detection_no_unhandled_exception():
    """Real regression for the heartbeat/detector race (plan section 11):
    run a live heartbeat thread against a worker whose stored heartbeat
    starts out stale, racing a detection cycle. Neither side should raise,
    and the final state must be self-consistent (whichever wins is fine)."""
    stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=60)
    worker = _register_worker(status=WorkerStatus.AVAILABLE, last_heartbeat=stale_heartbeat)
    runtime = WorkerRuntime()
    runtime.worker = worker
    try:
        runtime.start_heartbeat(interval_seconds=0.01)

        detector = FailureDetector()
        for _ in range(20):
            detector.run_cycle()

        fetched = _fetch(worker.id)
        assert fetched.status in (WorkerStatus.AVAILABLE, WorkerStatus.UNHEALTHY)
    finally:
        runtime.stop_heartbeat()
        _cleanup(worker_id=worker.id)


def test_concurrent_dispatch_vs_detection_no_unhandled_exception():
    """Real regression for the dispatch/detector race (plan section 11):
    execute_task() claiming a worker (AVAILABLE -> BUSY) races a detection
    cycle trying AVAILABLE -> UNHEALTHY on the same worker. Neither side
    should raise; the loser is cleanly skipped."""
    job = Job(name="fd-race-job")
    task = Task(job_id=job.id, type="shell", payload={"command": "exit 0"})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)

    stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=60)
    worker = _register_worker(status=WorkerStatus.AVAILABLE, last_heartbeat=stale_heartbeat)
    runtime = WorkerRuntime()
    runtime.worker = worker
    detector = FailureDetector()

    errors = []

    def _dispatch():
        try:
            runtime.execute_task(task.id)
        except Exception as exc:  # noqa: BLE001 - InvalidTransitionError is an OK outcome
            from atlas.domain import InvalidTransitionError

            if not isinstance(exc, InvalidTransitionError):
                errors.append(exc)

    def _detect():
        try:
            detector.run_cycle()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_dispatch)
    t2 = threading.Thread(target=_detect)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    try:
        assert errors == []

        fetched_worker = _fetch(worker.id)
        assert fetched_worker.status in (WorkerStatus.AVAILABLE, WorkerStatus.UNHEALTHY)
    finally:
        _cleanup(job.id, worker.id)
