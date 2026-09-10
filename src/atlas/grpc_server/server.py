"""Minimal gRPC server bootstrap for the control plane's WorkerService."""

from concurrent import futures

import grpc

from atlas.config import get_settings
from atlas.grpc_server import atlas_pb2_grpc
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.service import WorkerServiceServicer


def build_server(registry: GrpcWorkerRegistry, port: int = 0) -> grpc.Server:
    """Build and bind (but do not start) a gRPC server. port=0 binds an
    ephemeral port (used by tests); returns the server with the actual
    bound port available via server.add_insecure_port's return value having
    already been applied — callers read it back via the port passed in or
    by inspecting the server after start() for port 0 bindings."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    atlas_pb2_grpc.add_WorkerServiceServicer_to_server(WorkerServiceServicer(registry), server)
    bound_port = server.add_insecure_port(f"[::]:{port}")
    server._atlas_bound_port = bound_port  # exposed for tests/callers using port=0
    return server


def serve_forever() -> None:
    settings = get_settings()
    registry = GrpcWorkerRegistry()
    server = build_server(registry, port=settings.grpc_port)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve_forever()
