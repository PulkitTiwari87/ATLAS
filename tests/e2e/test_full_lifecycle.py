"""E2E scenarios 6-9: happy-path job submission through multiple workers.

Scenarios 1-5 and 8 (job submission, SUBMITTED -> QUEUED, priority
ordering, dispatch, worker execution, TaskAttempt persistence) are already
covered by their owning phase's own integration tests (Phase 03/05/06/04
respectively) — see plans/phase-12-integration-e2e.md section 17's
scenario table. This file only adds the E2E-tier scenarios: full-loop
outcomes that need Scheduler + Dispatch + Worker running together, not
re-implementations of what those phases already test in isolation.

Skipped automatically when PostgreSQL is not reachable.
"""

import pytest
from sqlalchemy import text

from atlas.domain import TaskStatus
from atlas.persistence.db import _engine

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


def test_scenario_06_successful_task_end_to_end():
    with AtlasTestSystem() as system:
        worker = system.add_worker()
        job, tasks = system.submit_job(
            "e2e-success-job", priority=0, tasks=[("shell", {"command": "exit 0"})]
        )

        result = system.wait_for_task_status(tasks[0].id, TaskStatus.SUCCEEDED)

        assert result.status == TaskStatus.SUCCEEDED
        assert worker.worker.id is not None


def test_scenario_07_failed_task_end_to_end():
    with AtlasTestSystem() as system:
        system.add_worker()
        job, tasks = system.submit_job(
            "e2e-failure-job", priority=0, tasks=[("shell", {"command": "exit 7"})]
        )

        result = system.wait_for_task_status(tasks[0].id, TaskStatus.FAILED)

        assert result.status == TaskStatus.FAILED


def test_scenario_09_multiple_workers_both_complete():
    with AtlasTestSystem() as system:
        system.add_worker()
        system.add_worker()
        job_a, tasks_a = system.submit_job(
            "e2e-multi-a", priority=0, tasks=[("shell", {"command": "exit 0"})]
        )
        job_b, tasks_b = system.submit_job(
            "e2e-multi-b", priority=0, tasks=[("shell", {"command": "exit 0"})]
        )

        result_a = system.wait_for_task_status(tasks_a[0].id, TaskStatus.SUCCEEDED)
        result_b = system.wait_for_task_status(tasks_b[0].id, TaskStatus.SUCCEEDED)

        assert result_a.status == TaskStatus.SUCCEEDED
        assert result_b.status == TaskStatus.SUCCEEDED
        # both tasks actually ran (on one worker or two — either is a
        # correct outcome; the point is both completed, not serialization)
        assert {result_a.worker_id, result_b.worker_id} <= {
            w.worker.id for w in system._workers
        }
