"""RecoveryManager unit tests. No database."""

import threading
import time
import uuid
from unittest.mock import MagicMock, patch

from atlas.domain import Task, TaskStatus, Worker, WorkerStatus
from atlas.recovery import RecoveryManager


def _task(status=TaskStatus.RUNNING, attempt=0, max_retries=3, worker_id=None):
    return Task(
        job_id=uuid.uuid4(),
        type="shell",
        status=status,
        attempt=attempt,
        max_retries=max_retries,
        worker_id=worker_id or uuid.uuid4(),
    )


def _worker(status=WorkerStatus.UNHEALTHY):
    return Worker(hostname="h", address="h:1", status=status)


@patch("atlas.recovery.recovery.session_scope")
def test_task_below_retry_limit_ends_pending_with_worker_cleared(mock_session_scope):
    manager = RecoveryManager()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    task = _task(attempt=1, max_retries=3)
    manager._tasks.get = MagicMock(return_value=task)
    manager._tasks.update = MagicMock()

    manager._recover_task(task.id)

    assert task.status == TaskStatus.PENDING
    assert task.worker_id is None
    manager._tasks.update.assert_called_once()


@patch("atlas.recovery.recovery.session_scope")
def test_task_at_retry_limit_stays_failed_terminal(mock_session_scope):
    manager = RecoveryManager()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    task = _task(attempt=3, max_retries=3)
    original_worker_id = task.worker_id
    manager._tasks.get = MagicMock(return_value=task)
    manager._tasks.update = MagicMock()

    manager._recover_task(task.id)

    assert task.status == TaskStatus.FAILED
    assert task.worker_id == original_worker_id  # left as-is, not cleared


@patch("atlas.recovery.recovery.session_scope")
def test_assigned_orphan_recovers_same_as_running(mock_session_scope):
    manager = RecoveryManager()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    task = _task(status=TaskStatus.ASSIGNED, attempt=0, max_retries=3)
    manager._tasks.get = MagicMock(return_value=task)
    manager._tasks.update = MagicMock()

    manager._recover_task(task.id)

    assert task.status == TaskStatus.PENDING
    assert task.worker_id is None


@patch("atlas.recovery.recovery.session_scope")
def test_recover_task_never_increments_attempt(mock_session_scope):
    manager = RecoveryManager()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    task = _task(attempt=1, max_retries=3)
    manager._tasks.get = MagicMock(return_value=task)
    manager._tasks.update = MagicMock()

    manager._recover_task(task.id)

    assert task.attempt == 1


@patch("atlas.recovery.recovery.session_scope")
def test_worker_with_no_orphaned_tasks_still_reaches_dead(mock_session_scope):
    manager = RecoveryManager()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    worker = _worker()
    manager._workers.get = MagicMock(return_value=worker)
    manager._workers.update = MagicMock()
    manager._load_orphaned_tasks = MagicMock(return_value=[])

    manager._recover_worker(worker.id)

    assert worker.status == WorkerStatus.DEAD
    manager._workers.update.assert_called_once()


def test_run_forever_stops_cleanly():
    manager = RecoveryManager()
    with patch.object(manager, "run_cycle", return_value=None):
        thread = threading.Thread(target=manager.run_forever, args=(0.01,))
        thread.start()
        time.sleep(0.05)
        manager.stop()
        thread.join(timeout=1)

    assert not thread.is_alive()


def test_run_forever_survives_cycle_exception():
    manager = RecoveryManager()
    with patch.object(manager, "run_cycle", side_effect=RuntimeError("boom")):
        thread = threading.Thread(target=manager.run_forever, args=(0.01,))
        thread.start()
        time.sleep(0.05)
        manager.stop()
        thread.join(timeout=1)

    assert not thread.is_alive()
