"""Phase 13 hardening: malformed gRPC input must produce INVALID_ARGUMENT,
not an unhandled exception (UNKNOWN/INTERNAL). Real gRPC server + real
client, same pattern as tests/grpc/test_registration.py.

Skipped automatically when PostgreSQL is not reachable.
"""

import grpc
import pytest
from sqlalchemy import text

from atlas.grpc_server import atlas_pb2, atlas_pb2_grpc
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.server import build_server
from atlas.persistence.db import _engine


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
def stub():
    registry = GrpcWorkerRegistry()
    server = build_server(registry, port=0)
    port = server._atlas_bound_port
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    try:
        yield atlas_pb2_grpc.WorkerServiceStub(channel)
    finally:
        channel.close()
        server.stop(0)


def test_register_worker_with_malformed_uuid_returns_invalid_argument(stub):
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.RegisterWorker(
            atlas_pb2.RegisterWorkerRequest(
                worker_id="not-a-uuid", hostname="h", address="h:1"
            )
        )

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_register_worker_with_empty_hostname_returns_invalid_argument(stub):
    import uuid

    with pytest.raises(grpc.RpcError) as exc_info:
        stub.RegisterWorker(
            atlas_pb2.RegisterWorkerRequest(
                worker_id=str(uuid.uuid4()), hostname="", address="h:1"
            )
        )

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_heartbeat_with_malformed_uuid_returns_invalid_argument(stub):
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.Heartbeat(
            atlas_pb2.HeartbeatRequest(worker_id="also-not-a-uuid", timestamp="2024-01-01T00:00:00+00:00")
        )

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_heartbeat_with_malformed_timestamp_returns_invalid_argument(stub):
    import uuid

    with pytest.raises(grpc.RpcError) as exc_info:
        stub.Heartbeat(
            atlas_pb2.HeartbeatRequest(worker_id=str(uuid.uuid4()), timestamp="not-a-timestamp")
        )

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_valid_register_and_heartbeat_still_succeed(stub):
    import uuid
    from datetime import datetime, timezone

    from atlas.persistence.db import session_scope
    from atlas.persistence.models import WorkerRow

    worker_id = uuid.uuid4()
    try:
        response = stub.RegisterWorker(
            atlas_pb2.RegisterWorkerRequest(
                worker_id=str(worker_id), hostname="h", address="h:1"
            )
        )
        assert response.worker_id == str(worker_id)

        stub.Heartbeat(
            atlas_pb2.HeartbeatRequest(
                worker_id=str(worker_id), timestamp=datetime.now(timezone.utc).isoformat()
            )
        )  # must not raise
    finally:
        with session_scope() as session:
            row = session.get(WorkerRow, worker_id)
            if row:
                session.delete(row)
