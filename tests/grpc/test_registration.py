"""gRPC registration/heartbeat unit-ish tests against a real in-process
gRPC server + real PostgreSQL (skips if unreachable) — a full fake channel
is more effort than value here since grpc's in-process server/channel pair
already gives fast, isolated tests without a real socket round trip cost.
"""

import uuid
from datetime import datetime, timezone

import grpc
import pytest
from sqlalchemy import text

from atlas.domain import WorkerStatus
from atlas.grpc_server import atlas_pb2, atlas_pb2_grpc
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.server import build_server
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import WorkerRow
from atlas.persistence.repositories import WorkerRepository


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
def server_and_channel():
    registry = GrpcWorkerRegistry()
    server = build_server(registry, port=0)
    port = server._atlas_bound_port
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    try:
        yield channel, registry
    finally:
        channel.close()
        server.stop(0)


def _cleanup(worker_id):
    with session_scope() as session:
        row = session.get(WorkerRow, worker_id)
        if row:
            session.delete(row)


def test_register_worker_persists_available_row(server_and_channel):
    channel, _ = server_and_channel
    stub = atlas_pb2_grpc.WorkerServiceStub(channel)
    worker_id = uuid.uuid4()
    try:
        response = stub.RegisterWorker(
            atlas_pb2.RegisterWorkerRequest(
                worker_id=str(worker_id), hostname="h", address="h:1"
            )
        )

        assert response.worker_id == str(worker_id)
        with session_scope() as session:
            row = WorkerRepository().get(session, worker_id)
        assert row.status == WorkerStatus.AVAILABLE
    finally:
        _cleanup(worker_id)


def test_heartbeat_rpc_updates_last_heartbeat(server_and_channel):
    channel, _ = server_and_channel
    stub = atlas_pb2_grpc.WorkerServiceStub(channel)
    worker_id = uuid.uuid4()
    try:
        stub.RegisterWorker(
            atlas_pb2.RegisterWorkerRequest(
                worker_id=str(worker_id), hostname="h", address="h:1"
            )
        )

        timestamp = datetime.now(timezone.utc)
        stub.Heartbeat(
            atlas_pb2.HeartbeatRequest(worker_id=str(worker_id), timestamp=timestamp.isoformat())
        )

        with session_scope() as session:
            row = WorkerRepository().get(session, worker_id)
        assert row.last_heartbeat is not None
    finally:
        _cleanup(worker_id)
