"""WorkerRuntime unit tests against the real PostgreSQL service (skips if unreachable).

register()/shutdown() are cheap single-row operations, so these run against
the real database rather than a fake repository double, matching the
Phase 02/03 testing pattern.
"""

import uuid

import pytest
from sqlalchemy import text

from atlas.domain import WorkerStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import WorkerRow
from atlas.worker import TaskNotFoundError, WorkerDrainingError, WorkerRuntime


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


def _cleanup_worker(worker_id):
    with session_scope() as session:
        row = session.get(WorkerRow, worker_id)
        if row:
            session.delete(row)


def test_register_transitions_to_available():
    runtime = WorkerRuntime()
    try:
        worker = runtime.register()
        assert worker.status == WorkerStatus.AVAILABLE
    finally:
        _cleanup_worker(runtime.worker.id)


def test_shutdown_from_available_goes_to_dead():
    runtime = WorkerRuntime()
    try:
        runtime.register()
        worker = runtime.shutdown()
        assert worker.status == WorkerStatus.DEAD
    finally:
        _cleanup_worker(runtime.worker.id)


def test_execute_task_after_shutdown_raises():
    runtime = WorkerRuntime()
    try:
        runtime.register()
        runtime.shutdown()

        with pytest.raises(WorkerDrainingError):
            runtime.execute_task(uuid.uuid4())
    finally:
        _cleanup_worker(runtime.worker.id)


def test_execute_unknown_task_raises_not_found():
    runtime = WorkerRuntime()
    try:
        runtime.register()

        with pytest.raises(TaskNotFoundError):
            runtime.execute_task(uuid.uuid4())
    finally:
        _cleanup_worker(runtime.worker.id)
