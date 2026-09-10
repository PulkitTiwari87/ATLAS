"""WorkerServiceServicer: thin gRPC adapter over the existing Phase 02/07
repositories. No new business logic — see plans/phase-10-grpc.md section 12.
"""

import logging
import queue
import threading
import uuid
from datetime import datetime

import grpc

from atlas.domain import Worker, WorkerStatus
from atlas.grpc_server import atlas_pb2, atlas_pb2_grpc
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import WorkerRepository

logger = logging.getLogger("atlas.grpc_server")


def _parse_worker_id(raw: str, context):
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid worker_id: {raw!r}")


class WorkerServiceServicer(atlas_pb2_grpc.WorkerServiceServicer):
    def __init__(self, registry: GrpcWorkerRegistry):
        self._registry = registry
        self._workers = WorkerRepository()

    def RegisterWorker(self, request, context):
        worker_id = _parse_worker_id(request.worker_id, context)
        if not request.hostname or not request.address:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "hostname and address are required")

        worker = Worker(id=worker_id, hostname=request.hostname, address=request.address)
        with session_scope() as session:
            self._workers.add(session, worker)
        worker.transition_to(WorkerStatus.AVAILABLE)
        with session_scope() as session:
            self._workers.update(session, worker)
        return atlas_pb2.RegisterWorkerResponse(worker_id=str(worker.id))

    def Heartbeat(self, request, context):
        worker_id = _parse_worker_id(request.worker_id, context)
        try:
            timestamp = datetime.fromisoformat(request.timestamp)
        except (ValueError, TypeError):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid timestamp: {request.timestamp!r}")

        with session_scope() as session:
            self._workers.update_heartbeat(session, worker_id, timestamp)
        return atlas_pb2.HeartbeatResponse()

    def TaskChannel(self, request_iterator, context):
        outbox: "queue.Queue" = queue.Queue()
        registered_worker_id = None

        def read_incoming():
            nonlocal registered_worker_id
            try:
                for message in request_iterator:
                    if registered_worker_id is None:
                        registered_worker_id = uuid.UUID(message.worker_id)
                        self._registry.register(registered_worker_id, outbox)
                    if message.HasField("result"):
                        self._registry.deliver_result(message.result)
            except Exception:
                logger.exception("TaskChannel read loop failed")
            finally:
                if registered_worker_id is not None:
                    self._registry.unregister(registered_worker_id)
                outbox.put(None)  # unblock the yield loop below

        reader = threading.Thread(target=read_incoming, daemon=True)
        reader.start()

        while True:
            item = outbox.get()
            if item is None:
                break
            yield item
