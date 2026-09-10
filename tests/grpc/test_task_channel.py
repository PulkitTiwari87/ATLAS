"""End-to-end TaskChannel tests: real gRPC server + real GrpcWorkerClient
(with its own real WorkerRuntime) + real PostgreSQL. Proves Dispatcher
requires zero changes to deliver a task over the streaming transport
(plans/phase-10-grpc.md section 24 acceptance criteria).
"""

import time

import grpc
import pytest
from sqlalchemy import text

from atlas.domain import Job, Task, TaskStatus, WorkerStatus
from atlas.dispatch import Dispatcher
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.server import build_server
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository
from atlas.scheduler import Scheduler
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


def _make_job_and_task(payload):
    job = Job(name="grpc-dispatch-job")
    task = Task(job_id=job.id, type="shell", payload=payload)
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
    time.sleep(0.2)  # let the TaskChannel stream's initial hello land server-side
    return channel, client


def test_dispatch_delivers_task_over_grpc_stream_and_executes(server_and_registry):
    target, registry = server_and_registry
    job, task = _make_job_and_task({"command": "exit 0"})
    channel, client = _connect_client(target)
    try:
        dispatcher = Dispatcher(registry)
        result = dispatcher.dispatch(task, client.worker)

        assert result.status == "dispatched"

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED

        with session_scope() as session:
            attempts = list(
                session.query(TaskAttemptRow).filter(TaskAttemptRow.task_id == task.id)
            )
        assert len(attempts) == 1
        assert attempts[0].error is None
    finally:
        client.shutdown()
        channel.close()
        _cleanup(job.id, client.worker.id)


def test_dispatch_failed_task_over_grpc_stream(server_and_registry):
    target, registry = server_and_registry
    job, task = _make_job_and_task({"command": "exit 7"})
    channel, client = _connect_client(target)
    try:
        dispatcher = Dispatcher(registry)
        result = dispatcher.dispatch(task, client.worker)

        assert result.status == "dispatched"

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.FAILED
    finally:
        client.shutdown()
        channel.close()
        _cleanup(job.id, client.worker.id)


def test_full_scheduler_dispatch_grpc_flow(server_and_registry):
    target, registry = server_and_registry
    job, task = _make_job_and_task({"command": "exit 0"})
    channel, client = _connect_client(target)
    try:
        scheduler = Scheduler()
        dispatcher = Dispatcher(registry)

        proposals = scheduler.run_cycle()
        proposed_ids = [t.id for t, _ in proposals]
        assert task.id in proposed_ids

        results = dispatcher.dispatch_all(proposals)
        assert any(r.status == "dispatched" for r in results)

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.SUCCEEDED
    finally:
        client.shutdown()
        channel.close()
        _cleanup(job.id, client.worker.id)


def test_worker_not_connected_is_a_dispatch_failure(server_and_registry):
    target, registry = server_and_registry
    job, task = _make_job_and_task({"command": "exit 0"})
    from atlas.domain import Worker

    ghost_worker = Worker(hostname="ghost", address="ghost:0")
    try:
        dispatcher = Dispatcher(registry)
        result = dispatcher.dispatch(task, ghost_worker)

        assert result.status == "skipped"

        with session_scope() as session:
            fetched_task = TaskRepository().get(session, task.id)
        assert fetched_task.status == TaskStatus.PENDING
    finally:
        _cleanup(job.id)


def test_shutdown_closes_stream_and_worker_removed_from_registry(server_and_registry):
    target, registry = server_and_registry
    channel, client = _connect_client(target)
    worker_id = client.worker.id
    try:
        assert registry.get(worker_id) is not None

        client.shutdown()
        time.sleep(0.3)

        assert registry.get(worker_id) is None

        with session_scope() as session:
            fetched_worker = WorkerRepository().get(session, worker_id)
        assert fetched_worker.status == WorkerStatus.DEAD
    finally:
        channel.close()
        _cleanup(worker_id=worker_id)
