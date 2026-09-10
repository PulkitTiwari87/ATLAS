"""GrpcWorkerClient heartbeat-over-gRPC tests: proves heartbeats sent via
the RPC update last_heartbeat exactly as the Phase 07 direct-call version
did (plans/phase-10-grpc.md section 24 acceptance criterion).
"""

import time

import grpc
import pytest
from sqlalchemy import text

from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.server import build_server
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import WorkerRow
from atlas.persistence.repositories import WorkerRepository
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
        yield f"localhost:{port}"
    finally:
        server.stop(0)


def _cleanup(worker_id):
    with session_scope() as session:
        row = session.get(WorkerRow, worker_id)
        if row:
            session.delete(row)


def _last_heartbeat(worker_id):
    with session_scope() as session:
        row = session.get(WorkerRow, worker_id)
        return row.last_heartbeat if row else None


def test_heartbeat_thread_advances_last_heartbeat_over_grpc(server_and_registry):
    channel = grpc.insecure_channel(server_and_registry)
    client = GrpcWorkerClient(channel)
    try:
        client.register()  # starts real heartbeat thread over gRPC

        deadline = time.monotonic() + 3
        first = None
        while first is None and time.monotonic() < deadline:
            first = _last_heartbeat(client.worker.id)
            if first is None:
                time.sleep(0.05)
        assert first is not None

        time.sleep(0.2)
        second = _last_heartbeat(client.worker.id)
        assert second >= first
    finally:
        client.shutdown()
        channel.close()
        _cleanup(client.worker.id)


def test_shutdown_stops_heartbeat_over_grpc(server_and_registry):
    channel = grpc.insecure_channel(server_and_registry)
    client = GrpcWorkerClient(channel)
    try:
        client.register()
        time.sleep(0.2)

        client.shutdown()
        last_at_shutdown = _last_heartbeat(client.worker.id)
        time.sleep(0.3)
        last_after_wait = _last_heartbeat(client.worker.id)

        assert last_after_wait == last_at_shutdown
    finally:
        channel.close()
        _cleanup(client.worker.id)
