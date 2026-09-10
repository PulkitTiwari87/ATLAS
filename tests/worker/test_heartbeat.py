"""WorkerRuntime heartbeat unit tests. Repository call is mocked to keep
these timing-independent of an actual DB round trip."""

import time
from unittest.mock import MagicMock, patch

from atlas.domain import WorkerStatus
from atlas.worker import WorkerRuntime


def _runtime_with_mocked_repo():
    runtime = WorkerRuntime()
    runtime._workers = MagicMock()
    return runtime


@patch("atlas.worker.runtime.session_scope")
def test_send_heartbeat_calls_update_heartbeat_with_worker_id(mock_session_scope):
    runtime = _runtime_with_mocked_repo()
    mock_session = MagicMock()
    mock_session_scope.return_value.__enter__.return_value = mock_session

    runtime.send_heartbeat()

    args, kwargs = runtime._workers.update_heartbeat.call_args
    assert args[0] is mock_session
    assert args[1] == runtime.worker.id


@patch("atlas.worker.runtime.session_scope")
def test_start_then_stop_heartbeat_joins_thread_cleanly(mock_session_scope):
    runtime = _runtime_with_mocked_repo()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()

    runtime.start_heartbeat(interval_seconds=0.01)
    thread = runtime._heartbeat_thread
    assert thread is not None
    assert thread.is_alive()

    runtime.stop_heartbeat()

    assert not thread.is_alive()
    assert runtime._heartbeat_thread is None


def test_stop_heartbeat_before_start_is_a_safe_noop():
    runtime = _runtime_with_mocked_repo()

    runtime.stop_heartbeat()  # must not raise

    assert runtime._heartbeat_thread is None


@patch("atlas.worker.runtime.session_scope")
def test_start_heartbeat_is_idempotent(mock_session_scope):
    runtime = _runtime_with_mocked_repo()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()

    runtime.start_heartbeat(interval_seconds=0.01)
    first_thread = runtime._heartbeat_thread
    runtime.start_heartbeat(interval_seconds=0.01)

    assert runtime._heartbeat_thread is first_thread

    runtime.stop_heartbeat()


@patch("atlas.worker.runtime.session_scope")
def test_repeated_heartbeat_ticks_occur(mock_session_scope):
    runtime = _runtime_with_mocked_repo()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()

    runtime.start_heartbeat(interval_seconds=0.01)
    time.sleep(0.1)
    runtime.stop_heartbeat()

    assert runtime._workers.update_heartbeat.call_count >= 2


@patch("atlas.worker.runtime.session_scope")
def test_heartbeat_never_changes_worker_status(mock_session_scope):
    runtime = _runtime_with_mocked_repo()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    runtime.worker.status = WorkerStatus.BUSY

    runtime.start_heartbeat(interval_seconds=0.01)
    time.sleep(0.05)
    runtime.stop_heartbeat()

    assert runtime.worker.status == WorkerStatus.BUSY


@patch("atlas.worker.runtime.session_scope")
def test_heartbeat_failure_does_not_kill_the_loop(mock_session_scope):
    runtime = _runtime_with_mocked_repo()
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    runtime._workers.update_heartbeat.side_effect = [RuntimeError("db down"), None, None]

    runtime.start_heartbeat(interval_seconds=0.01)
    time.sleep(0.1)
    thread = runtime._heartbeat_thread
    still_alive_after_failure = thread.is_alive()
    runtime.stop_heartbeat()

    assert still_alive_after_failure
    assert runtime._workers.update_heartbeat.call_count >= 2
