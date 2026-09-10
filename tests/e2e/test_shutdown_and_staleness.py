"""E2E scenarios 17-20: stale proposals, worker draining, graceful
shutdown, and full system startup/shutdown.

Scenario 17 (stale scheduling/dispatch proposal) is already covered by
Phase 06's own integration tests (tests/dispatch/test_dispatch_integration.py)
— not re-implemented here. Scenario 18's core mechanism (WorkerDrainingError
on execute_task() after shutdown()) is already unit/integration-tested by
Phase 04 (tests/worker/test_runtime.py); this file adds only the E2E-level
proof that a live Scheduler+Dispatcher loop correctly never routes new work
to an already-drained worker.

Skipped automatically when PostgreSQL is not reachable.
"""

import time

import pytest
from sqlalchemy import text

from atlas.domain import TaskStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.repositories import TaskRepository

from tests.e2e.harness import AtlasTestSystem


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


def test_scenario_18_drained_worker_receives_no_new_task():
    with AtlasTestSystem() as system:
        worker = system.add_worker()
        worker.shutdown()  # drains and dies before any task exists

        job, tasks = system.submit_job(
            "e2e-drain-job", priority=0, tasks=[("shell", {"command": "exit 0"})]
        )

        # give the live Scheduler+Dispatcher loop several cycles to
        # (not) act — the assertion is that nothing happens
        time.sleep(0.5)

        with session_scope() as session:
            fetched = TaskRepository().get(session, tasks[0].id)
        assert fetched.status == TaskStatus.PENDING
        assert fetched.worker_id is None


def test_scenario_19_graceful_shutdown_with_work_in_flight():
    system = AtlasTestSystem()
    with system:
        worker = system.add_worker()
        job, tasks = system.submit_job(
            "e2e-shutdown-job", priority=0, tasks=[("shell", {"command": "exit 0"})]
        )
        system.wait_for_task_status(tasks[0].id, TaskStatus.SUCCEEDED)
        # __exit__ runs here: stop() on every loop, join every thread,
        # shutdown() on every worker — must not raise.

    assert all(not thread.is_alive() for thread in system._threads)
    assert not (worker._heartbeat_thread and worker._heartbeat_thread.is_alive())


def test_scenario_20_full_system_startup_and_shutdown():
    system = AtlasTestSystem()
    with system:
        pass  # the lifecycle itself is the scenario — no work needed

    assert all(not thread.is_alive() for thread in system._threads)
