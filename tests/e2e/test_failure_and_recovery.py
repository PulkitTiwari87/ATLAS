"""E2E scenarios 14-16: retry, retry exhaustion, and duplicate execution.

Scenarios 10-13 (worker heartbeat, worker becomes stale, failure
detection, task recovery) are already covered by their owning phase's own
integration tests (Phase 07/08/08/09 respectively) — see
plans/phase-12-integration-e2e.md section 17. Not re-implemented here.

Fault injection technique for 14/15/16 (two deliberate, scenario-specific
points, explicitly sanctioned by the plan's tier definition — not general
mocking):

1. WorkerRuntime._finish() on one specific worker instance is patched to
   raise instead of persisting, simulating a worker that dies in the
   exact gap Phase 09 section 8 documents — after its executor has
   already run (real side effects happened) but before the result is
   reported/persisted.

2. That worker's `last_heartbeat` is set directly (via the same
   WorkerRepository.update_heartbeat Phase 07 already uses) to an hour in
   the past, then FailureDetector.run_cycle()/RecoveryManager.run_cycle()
   are each called once, directly. An earlier version of this file tried
   to reproduce staleness by racing a real background heartbeat thread
   against an artificially-shortened worker_timeout — that was flaky
   under this environment's thread/DB-connection-pool contention (6+
   concurrent threads), because detection ends up racing a real wall
   clock against a real thread-scheduling delay that has no reliable
   upper bound here. Setting the timestamp directly and driving detection
   with one explicit call removes the race entirely: no worker_timeout
   patching, no threshold to lose to jitter — the same public
   repository/loop APIs, called deterministically instead of raced.
   Everything else (Scheduler, Dispatcher, the harness's own background
   loops, the second worker) is real, running against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from atlas.domain import TaskStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.repositories import TaskRepository, WorkerRepository

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


def _crash_after_work_completes(*_args, **_kwargs):
    raise ConnectionError("simulated crash: worker died after executing, before reporting")


def _run_and_ignore_crash(worker, task_id) -> None:
    try:
        worker.execute_task(task_id)
    except Exception:
        pass  # the simulated crash — expected, not a test failure


def _dispatch_and_crash(worker, task_id) -> None:
    """Runs task_id to real completion on `worker`, then makes it 'die'
    right before persisting the result (see module docstring, point 1)."""
    worker._finish = _crash_after_work_completes
    thread = threading.Thread(target=_run_and_ignore_crash, args=(worker, task_id), daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "simulated execution did not complete in time"


def _mark_stale_and_recover(system, worker) -> None:
    """Deterministically ages `worker`'s heartbeat and drives one real
    FailureDetector + RecoveryManager cycle — see module docstring,
    point 2. No worker_timeout patching, no wall-clock race."""
    worker.stop_heartbeat()  # no clean shutdown() — simulates the crash becoming visible
    ancient = datetime.now(timezone.utc) - timedelta(hours=1)
    with session_scope() as session:
        WorkerRepository().update_heartbeat(session, worker.worker.id, ancient)
    system.failure_detector.run_cycle()
    system.recovery_manager.run_cycle()


def test_scenario_14_task_is_recovered_and_retried_on_a_second_worker():
    with AtlasTestSystem() as system:
        worker_a = system.add_worker()
        job, tasks = system.submit_job(
            "e2e-retry-job", priority=0, tasks=[("shell", {"command": "exit 0"})]
        )
        task = tasks[0]

        _dispatch_and_crash(worker_a, task.id)
        _mark_stale_and_recover(system, worker_a)

        worker_b = system.add_worker()

        result = system.wait_for_task_status(task.id, TaskStatus.SUCCEEDED, timeout=10)

        assert result.status == TaskStatus.SUCCEEDED
        assert result.worker_id == worker_b.worker.id
        assert result.attempt == 2  # execution #1 (worker_a) + execution #2 (worker_b)


def test_scenario_15_retry_exhaustion_ends_terminal_failed():
    with AtlasTestSystem() as system:
        worker_a = system.add_worker()
        job, tasks = system.submit_job(
            "e2e-retry-exhaustion-job",
            priority=0,
            tasks=[("shell", {"command": "exit 0"})],
        )
        task = tasks[0]
        with session_scope() as session:
            fetched = TaskRepository().get(session, task.id)
            fetched.max_retries = 0
            TaskRepository().update(session, fetched)

        _dispatch_and_crash(worker_a, task.id)
        _mark_stale_and_recover(system, worker_a)

        result = system.wait_for_task_status(task.id, TaskStatus.FAILED, timeout=10)

        assert result.status == TaskStatus.FAILED
        assert result.attempt == 1  # never redispatched — attempt(1) >= max_retries(0)


def test_scenario_16_duplicate_execution_is_observable(tmp_path):
    """Documentation-oriented: proves the Phase 09 section 8 risk is real
    at the level of actual side effects (a real file written twice by two
    real shell executions), not just DB bookkeeping. This test PASSES
    when duplicate execution is observed — that is the expected, accepted
    outcome of at-least-once semantics, not a bug."""
    marker = tmp_path / "marker.txt"
    with AtlasTestSystem() as system:
        worker_a = system.add_worker()
        job, tasks = system.submit_job(
            "e2e-duplicate-job",
            priority=0,
            tasks=[("shell", {"command": f"echo ran >> {marker}"})],
        )
        task = tasks[0]

        _dispatch_and_crash(worker_a, task.id)
        _mark_stale_and_recover(system, worker_a)

        worker_b = system.add_worker()

        result = system.wait_for_task_status(task.id, TaskStatus.SUCCEEDED, timeout=10)

        assert result.status == TaskStatus.SUCCEEDED
        assert result.worker_id == worker_b.worker.id
        assert result.attempt == 2

        # The real assertion: the shell command actually ran twice — once
        # on worker_a (real side effect, persistence lost to the
        # simulated crash) and again on worker_b (real side effect,
        # persisted) — genuine duplicate execution, exactly as Phase 09
        # section 8 documents as possible and explicitly does not attempt
        # to prevent.
        lines = marker.read_text().splitlines()
        assert len(lines) == 2, (
            "expected the shell command to have run twice (duplicate "
            f"execution is accepted at-least-once behavior, not a bug) — "
            f"found {len(lines)} marker lines"
        )
