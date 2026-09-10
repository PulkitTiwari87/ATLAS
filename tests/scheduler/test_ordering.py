"""Scheduler ordering/eligibility unit tests. No database."""

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from atlas.domain import Task, TaskStatus, Worker, WorkerStatus
from atlas.scheduler import Scheduler


def _task(job_id=None, created_at=None, status=TaskStatus.PENDING):
    return Task(
        job_id=job_id or uuid.uuid4(),
        type="shell",
        payload={},
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _worker(status=WorkerStatus.AVAILABLE):
    return Worker(hostname="h", address="h:1", status=status)


def test_order_by_priority_prefers_higher_job_priority():
    scheduler = Scheduler()
    job_high, job_low = uuid.uuid4(), uuid.uuid4()
    low_priority_task = _task(job_id=job_low)
    high_priority_task = _task(job_id=job_high)

    # priorities aren't known without a DB lookup in this unit test, so
    # patch the priority loader directly to isolate the ordering logic.
    priorities = {job_high: 10, job_low: 1}
    scheduler._load_job_priorities = lambda job_ids: priorities

    ordered = scheduler._order_by_priority([low_priority_task, high_priority_task])

    assert ordered == [high_priority_task, low_priority_task]


def test_order_by_priority_fifo_within_equal_priority():
    scheduler = Scheduler()
    job_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    older_task = _task(job_id=job_id, created_at=now - timedelta(seconds=5))
    newer_task = _task(job_id=job_id, created_at=now)

    scheduler._load_job_priorities = lambda job_ids: {job_id: 5}

    ordered = scheduler._order_by_priority([newer_task, older_task])

    assert ordered == [older_task, newer_task]


def test_order_by_priority_empty_list():
    scheduler = Scheduler()

    assert scheduler._order_by_priority([]) == []


def test_run_cycle_pairs_at_most_min_of_tasks_and_workers(monkeypatch):
    scheduler = Scheduler()
    job_id = uuid.uuid4()
    tasks = [_task(job_id=job_id) for _ in range(3)]
    workers = [_worker() for _ in range(2)]

    monkeypatch.setattr(scheduler, "_load_runnable_tasks", lambda: tasks)
    monkeypatch.setattr(scheduler, "_advance_submitted_jobs", lambda runnable: None)
    monkeypatch.setattr(scheduler, "_load_available_workers", lambda: workers)
    monkeypatch.setattr(scheduler, "_load_job_priorities", lambda ids: {job_id: 0})

    proposals = scheduler.run_cycle()

    assert len(proposals) == 2


def test_run_cycle_empty_queue_returns_empty_list(monkeypatch):
    scheduler = Scheduler()

    monkeypatch.setattr(scheduler, "_load_runnable_tasks", lambda: [])
    monkeypatch.setattr(scheduler, "_advance_submitted_jobs", lambda runnable: None)
    monkeypatch.setattr(scheduler, "_load_available_workers", lambda: [])

    assert scheduler.run_cycle() == []


def test_run_forever_stops_cleanly(monkeypatch):
    scheduler = Scheduler()
    calls = []
    monkeypatch.setattr(scheduler, "run_cycle", lambda: calls.append(1))

    thread = threading.Thread(target=scheduler.run_forever, args=(0.01,))
    thread.start()
    time.sleep(0.05)
    scheduler.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(calls) >= 1


def test_run_forever_survives_cycle_exception(monkeypatch):
    scheduler = Scheduler()

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "run_cycle", _raise)

    thread = threading.Thread(target=scheduler.run_forever, args=(0.01,))
    thread.start()
    time.sleep(0.05)
    scheduler.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()


def test_run_cycle_propagates_exceptions_directly(monkeypatch):
    scheduler = Scheduler()
    monkeypatch.setattr(
        scheduler,
        "_load_runnable_tasks",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with pytest.raises(RuntimeError):
        scheduler.run_cycle()
