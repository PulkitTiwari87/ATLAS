"""FailureDetector unit tests. No database."""

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from atlas.domain import Worker, WorkerStatus
from atlas.failure_detection import FailureDetector


def _worker(status=WorkerStatus.AVAILABLE, last_heartbeat=None):
    return Worker(hostname="h", address="h:1", status=status, last_heartbeat=last_heartbeat)


@patch("atlas.failure_detection.detector.get_settings")
def test_stale_worker_is_flagged(mock_settings):
    mock_settings.return_value.worker_timeout = 30
    detector = FailureDetector()
    stale = _worker(last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=60))

    assert detector._is_stale(stale) is True


@patch("atlas.failure_detection.detector.get_settings")
def test_fresh_worker_is_not_flagged(mock_settings):
    mock_settings.return_value.worker_timeout = 30
    detector = FailureDetector()
    fresh = _worker(last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=1))

    assert detector._is_stale(fresh) is False


@patch("atlas.failure_detection.detector.get_settings")
def test_none_heartbeat_is_flagged(mock_settings):
    mock_settings.return_value.worker_timeout = 30
    detector = FailureDetector()
    never_beat = _worker(last_heartbeat=None)

    assert detector._is_stale(never_beat) is True


@patch("atlas.failure_detection.detector.get_settings")
def test_threshold_boundary_is_exclusive(mock_settings):
    # Real elapsed wall-clock time between constructing "30s ago" and the
    # detector's own datetime.now() call would always push the delta past
    # 30s, making this flaky by construction if tested against real time.
    # Freeze "now" instead so the boundary comparison is exact.
    mock_settings.return_value.worker_timeout = 30
    detector = FailureDetector()
    fixed_now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    exactly_at_threshold = _worker(last_heartbeat=fixed_now - timedelta(seconds=30))

    with patch("atlas.failure_detection.detector.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        assert detector._is_stale(exactly_at_threshold) is False


def test_only_available_and_busy_are_candidate_statuses():
    from atlas.failure_detection.detector import _CANDIDATE_STATUSES

    assert set(_CANDIDATE_STATUSES) == {WorkerStatus.AVAILABLE, WorkerStatus.BUSY}
    assert WorkerStatus.DRAINING not in _CANDIDATE_STATUSES
    assert WorkerStatus.DEAD not in _CANDIDATE_STATUSES
    assert WorkerStatus.REGISTERING not in _CANDIDATE_STATUSES
    assert WorkerStatus.UNHEALTHY not in _CANDIDATE_STATUSES


def test_run_forever_stops_cleanly():
    detector = FailureDetector()
    with patch.object(detector, "run_cycle", return_value=None):
        thread = threading.Thread(target=detector.run_forever, args=(0.01,))
        thread.start()
        time.sleep(0.05)
        detector.stop()
        thread.join(timeout=1)

    assert not thread.is_alive()


def test_run_forever_survives_cycle_exception():
    detector = FailureDetector()
    with patch.object(detector, "run_cycle", side_effect=RuntimeError("boom")):
        thread = threading.Thread(target=detector.run_forever, args=(0.01,))
        thread.start()
        time.sleep(0.05)
        detector.stop()
        thread.join(timeout=1)

    assert not thread.is_alive()


@patch("atlas.failure_detection.detector.session_scope")
def test_mark_unhealthy_tolerates_worker_no_longer_eligible(mock_session_scope):
    from unittest.mock import MagicMock

    detector = FailureDetector()
    session = MagicMock()
    mock_session_scope.return_value.__enter__.return_value = session
    already_draining = _worker(status=WorkerStatus.DRAINING)
    detector._workers.get = MagicMock(return_value=already_draining)
    detector._workers.update = MagicMock()

    detector._mark_unhealthy(already_draining.id)  # must not raise

    detector._workers.update.assert_not_called()


@patch("atlas.failure_detection.detector.session_scope")
def test_one_worker_race_does_not_abort_the_cycle(mock_session_scope):
    """A worker that raced away (e.g. shutdown/dispatch won first) and is no
    longer AVAILABLE/BUSY by the time _mark_unhealthy re-fetches it is
    tolerated in-place (per plan section 11) — the cycle still processes
    every other candidate."""
    from unittest.mock import MagicMock

    detector = FailureDetector()
    session = MagicMock()
    mock_session_scope.return_value.__enter__.return_value = session

    stale_a = _worker(last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=60))
    stale_b = _worker(last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=60))
    detector._load_candidates = MagicMock(return_value=[stale_a, stale_b])

    # worker a raced to DRAINING between load and mark; worker b is untouched
    raced_away = _worker(status=WorkerStatus.DRAINING)

    def fake_get(session, worker_id):
        return raced_away if worker_id == stale_a.id else stale_b

    detector._workers.get = fake_get
    detector._workers.update = MagicMock()

    detector.run_cycle()  # must not raise

    detector._workers.update.assert_called_once()
    updated_worker = detector._workers.update.call_args[0][1]
    assert updated_worker.id == stale_b.id
    assert updated_worker.status == WorkerStatus.UNHEALTHY
