"""Regression test for the Phase 10 late-result race:

    execution #1 on Worker A
    -> Worker A becomes UNHEALTHY
    -> Phase 09 recovers Task X (attempt stays 1, worker_id cleared, PENDING)
    -> Task X is dispatched as execution #2 (attempt 2) to Worker B
    -> Worker B waits for Task X's result
    -> execution #1 sends a late TaskResult for attempt 1

Before the fix, GrpcWorkerRegistry correlated results by task_id alone, so
a second dispatch's waiter registration for the same task_id would
overwrite the first in the waiters dict — routing a late result from an
abandoned execution to whichever waiter happened to occupy that dict slot
at delivery time, regardless of which execution it actually belonged to.

The fix keys waiters by (task_id, attempt), reusing Task.attempt (Phase
01/04's existing execution identity, incremented once per RUNNING entry,
untouched by Phase 09 recovery) — so a stale attempt can never collide
with a newer one's key.

This test does not re-drive Phase 09's own recovery logic (already tested
in tests/recovery/) — it starts directly from recovery's documented
postcondition: a task with attempt=1, status=PENDING, worker_id=None, as
if execution #1 had already been recovered.
"""

import threading
import time
import uuid

import grpc
import pytest
from sqlalchemy import text

from atlas.domain import Job, Task, TaskStatus
from atlas.dispatch import Dispatcher
from atlas.grpc_server import atlas_pb2
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.server import build_server
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository
from atlas.worker.grpc_client import GrpcWorkerClient


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


@pytest.fixture
def server_and_registry():
    registry = GrpcWorkerRegistry()
    server = build_server(registry, port=0)
    port = server._atlas_bound_port
    server.start()
    try:
        yield f"localhost:{port}", registry
    finally:
        server.stop(0)


def _make_task_already_recovered_once(payload, attempt=1):
    """Sets up the exact postcondition Phase 09's RecoveryManager leaves
    behind after recovering an orphaned execution #1: attempt already
    incremented, worker_id cleared, status PENDING."""
    job = Job(name="late-result-race-job")
    task = Task(job_id=job.id, type="shell", payload=payload, attempt=attempt)
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)
    return job, task


def _cleanup(job_id=None, worker_id=None):
    with session_scope() as session:
        if job_id:
            for row in session.query(TaskAttemptRow).join(
                TaskRow, TaskAttemptRow.task_id == TaskRow.id
            ).filter(TaskRow.job_id == job_id):
                session.delete(row)
            for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
                session.delete(row)
            job_row = session.get(JobRow, job_id)
            if job_row:
                session.delete(job_row)
        if worker_id:
            worker_row = session.get(WorkerRow, worker_id)
            if worker_row:
                session.delete(worker_row)


def _connect_client(target):
    channel = grpc.insecure_channel(target)
    client = GrpcWorkerClient(channel)
    client.register()
    time.sleep(0.2)
    return channel, client


def _wait_for_waiter_registered(registry, key, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with registry._lock:
            if key in registry._waiters:
                return True
        time.sleep(0.01)
    return False


def test_late_result_from_prior_execution_does_not_satisfy_next_execution(server_and_registry):
    target, registry = server_and_registry
    job, task = _make_task_already_recovered_once({"command": "exit 0"}, attempt=1)
    channel, worker_b = _connect_client(target)
    try:
        dispatcher = Dispatcher(registry)
        outcome = {}

        def run_execution_two():
            outcome["result"] = dispatcher.dispatch(task, worker_b.worker)

        thread = threading.Thread(target=run_execution_two)
        thread.start()

        # deterministically wait for execution #2's own waiter, keyed
        # (task_id, 2), to be registered before injecting the stale result
        assert _wait_for_waiter_registered(registry, (str(task.id), 2))

        # execution #1's late result arrives — same task_id, OLD attempt.
        # Pre-fix, this would have silently satisfied whichever waiter
        # currently occupied the task_id-only dict slot (execution #2's).
        registry.deliver_result(
            atlas_pb2.TaskResult(task_id=str(task.id), attempt=1, skipped=False)
        )

        thread.join(timeout=10)
        assert not thread.is_alive()

        # execution #2 must have been satisfied by worker_b's own real
        # result (attempt 2), not the injected stale attempt-1 result.
        assert outcome["result"].status == "dispatched"

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
        assert fetched_task.attempt == 2  # incremented once, by worker_b's real _start()
    finally:
        worker_b.shutdown()
        channel.close()
        _cleanup(job.id, worker_b.worker.id)


def test_late_result_with_wrong_attempt_is_skipped_status(server_and_registry):
    """If the stale result had instead carried skipped=True (e.g. worker A
    reporting a draining/stale error for its own abandoned execution), it
    still must not be able to force execution #2 into a skipped outcome."""
    target, registry = server_and_registry
    job, task = _make_task_already_recovered_once({"command": "exit 0"}, attempt=1)
    channel, worker_b = _connect_client(target)
    try:
        dispatcher = Dispatcher(registry)
        outcome = {}

        def run_execution_two():
            outcome["result"] = dispatcher.dispatch(task, worker_b.worker)

        thread = threading.Thread(target=run_execution_two)
        thread.start()

        assert _wait_for_waiter_registered(registry, (str(task.id), 2))

        registry.deliver_result(
            atlas_pb2.TaskResult(
                task_id=str(task.id), attempt=1, skipped=True, error="stale skip from execution #1"
            )
        )

        thread.join(timeout=10)
        assert not thread.is_alive()

        assert outcome["result"].status == "dispatched"
        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
    finally:
        worker_b.shutdown()
        channel.close()
        _cleanup(job.id, worker_b.worker.id)


def test_unmatched_result_is_safely_ignored_no_waiter_registered():
    registry = GrpcWorkerRegistry()

    registry.deliver_result(
        atlas_pb2.TaskResult(task_id=str(uuid.uuid4()), attempt=1, skipped=False)
    )  # must not raise

    with registry._lock:
        assert registry._waiters == {}
