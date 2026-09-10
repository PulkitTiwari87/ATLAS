"""Dispatcher/WorkerRegistry/DispatchLoop unit tests. No database."""

import uuid

import pytest

from atlas.domain import InvalidTransitionError, Task, TaskStatus, Worker
from atlas.dispatch import DispatchLoop, Dispatcher, WorkerRegistry
from atlas.worker.errors import WorkerDrainingError


def _task():
    return Task(job_id=uuid.uuid4(), type="shell", payload={})


def _worker():
    return Worker(hostname="h", address="h:1")


class _FakeRuntime:
    def __init__(self, worker, effect=None):
        self.worker = worker
        self.calls = []
        self._effect = effect

    def execute_task(self, task_id):
        self.calls.append(task_id)
        if self._effect is not None:
            raise self._effect
        return "ok"


def test_registry_register_get_unregister():
    worker = _worker()
    runtime = _FakeRuntime(worker)
    registry = WorkerRegistry()

    assert registry.get(worker.id) is None

    registry.register(runtime)
    assert registry.get(worker.id) is runtime

    registry.unregister(worker.id)
    assert registry.get(worker.id) is None


def test_dispatch_unregistered_worker_is_skipped():
    task, worker = _task(), _worker()
    dispatcher = Dispatcher(WorkerRegistry())

    result = dispatcher.dispatch(task, worker)

    assert result.status == "skipped"
    assert result.reason == "worker_not_registered"


def test_dispatch_catches_invalid_transition_error():
    task, worker = _task(), _worker()
    registry = WorkerRegistry()
    runtime = _FakeRuntime(
        worker, effect=InvalidTransitionError("Task", TaskStatus.ASSIGNED, TaskStatus.ASSIGNED)
    )
    registry.register(runtime)
    dispatcher = Dispatcher(registry)

    result = dispatcher.dispatch(task, worker)

    assert result.status == "skipped"
    assert runtime.calls == [task.id]


def test_dispatch_catches_worker_draining_error():
    task, worker = _task(), _worker()
    registry = WorkerRegistry()
    runtime = _FakeRuntime(worker, effect=WorkerDrainingError("draining"))
    registry.register(runtime)
    dispatcher = Dispatcher(registry)

    result = dispatcher.dispatch(task, worker)

    assert result.status == "skipped"


def test_dispatch_success():
    task, worker = _task(), _worker()
    registry = WorkerRegistry()
    runtime = _FakeRuntime(worker)
    registry.register(runtime)
    dispatcher = Dispatcher(registry)

    result = dispatcher.dispatch(task, worker)

    assert result.status == "dispatched"
    assert runtime.calls == [task.id]


def test_dispatch_all_processes_proposals_sequentially_in_order():
    registry = WorkerRegistry()
    call_order = []
    proposals = []
    for i in range(3):
        task, worker = _task(), _worker()
        runtime = _FakeRuntime(worker)
        original_execute = runtime.execute_task

        def make_tracked(idx, fn):
            def tracked(task_id):
                call_order.append(idx)
                return fn(task_id)
            return tracked

        runtime.execute_task = make_tracked(i, original_execute)
        registry.register(runtime)
        proposals.append((task, worker))

    dispatcher = Dispatcher(registry)
    results = dispatcher.dispatch_all(proposals)

    assert call_order == [0, 1, 2]
    assert [r.status for r in results] == ["dispatched", "dispatched", "dispatched"]


def test_dispatch_loop_run_cycle_calls_scheduler_then_dispatcher():
    calls = []

    class _FakeScheduler:
        def run_cycle(self):
            calls.append("scheduler")
            return ["proposal"]

    class _FakeDispatcher:
        def dispatch_all(self, proposals):
            calls.append(("dispatcher", proposals))
            return []

    loop = DispatchLoop(_FakeScheduler(), _FakeDispatcher())
    loop.run_cycle()

    assert calls == ["scheduler", ("dispatcher", ["proposal"])]


def test_dispatch_loop_stops_cleanly():
    import threading
    import time

    class _FakeScheduler:
        def run_cycle(self):
            return []

    class _FakeDispatcher:
        def dispatch_all(self, proposals):
            return []

    loop = DispatchLoop(_FakeScheduler(), _FakeDispatcher())
    thread = threading.Thread(target=loop.run_forever, args=(0.01,))
    thread.start()
    time.sleep(0.05)
    loop.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()


def test_dispatch_loop_survives_cycle_exception():
    import threading
    import time

    class _FakeScheduler:
        def run_cycle(self):
            raise RuntimeError("boom")

    class _FakeDispatcher:
        def dispatch_all(self, proposals):
            return []

    loop = DispatchLoop(_FakeScheduler(), _FakeDispatcher())
    thread = threading.Thread(target=loop.run_forever, args=(0.01,))
    thread.start()
    time.sleep(0.05)
    loop.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()
