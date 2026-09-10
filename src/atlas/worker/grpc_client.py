"""GrpcWorkerClient: the worker-process side of Phase 10's transport.

register()/heartbeat/task-delivery go over gRPC. Actual task claim,
execution, and result persistence stay entirely inside the wrapped
WorkerRuntime, unchanged from Phase 04 — this process still holds its own
direct PostgreSQL connection for that (plans/phase-10-grpc.md sections 6/7).
"""

import logging
import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import grpc

from atlas.domain import InvalidTransitionError, WorkerStatus
from atlas.grpc_server import atlas_pb2, atlas_pb2_grpc
from atlas.worker.errors import TaskNotFoundError, WorkerDrainingError
from atlas.worker.runtime import WorkerRuntime

logger = logging.getLogger("atlas.worker.grpc_client")

_SKIPPABLE_ERRORS = (InvalidTransitionError, WorkerDrainingError, TaskNotFoundError)


class GrpcWorkerClient:
    def __init__(self, channel: grpc.Channel):
        self._stub = atlas_pb2_grpc.WorkerServiceStub(channel)
        self.runtime = WorkerRuntime()
        self._outgoing: "queue.Queue" = queue.Queue()
        self._stream_stop = threading.Event()
        self._stream_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()

    @property
    def worker(self):
        return self.runtime.worker

    def register(self) -> None:
        response = self._stub.RegisterWorker(
            atlas_pb2.RegisterWorkerRequest(
                worker_id=str(self.runtime.worker.id),
                hostname=self.runtime.worker.hostname,
                address=self.runtime.worker.address,
            )
        )
        assert response.worker_id == str(self.runtime.worker.id)
        self.runtime.worker.transition_to(WorkerStatus.AVAILABLE)
        self._start_task_channel()
        self._start_heartbeat()

    def shutdown(self) -> None:
        self._stop_heartbeat()
        self._stop_task_channel()
        self.runtime.shutdown()

    def _start_task_channel(self) -> None:
        self._stream_stop.clear()

        def request_generator():
            self._outgoing.put(atlas_pb2.WorkerMessage(worker_id=str(self.runtime.worker.id)))
            while not self._stream_stop.is_set():
                try:
                    message = self._outgoing.get(timeout=1)
                except queue.Empty:
                    continue
                if message is None:
                    break
                yield message

        def handle_stream():
            try:
                for server_message in self._stub.TaskChannel(request_generator()):
                    if server_message.HasField("dispatch"):
                        self._handle_dispatch(server_message.dispatch)
            except grpc.RpcError:
                if not self._stream_stop.is_set():
                    logger.exception("TaskChannel stream failed for worker %s", self.runtime.worker.id)

        self._stream_thread = threading.Thread(target=handle_stream, daemon=True)
        self._stream_thread.start()

    def _stop_task_channel(self) -> None:
        self._stream_stop.set()
        self._outgoing.put(None)
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=5)
            self._stream_thread = None

    def _handle_dispatch(self, dispatch_msg) -> None:
        task_id = uuid.UUID(dispatch_msg.task_id)
        # Echo dispatch_msg.attempt back unchanged — it's the correlation
        # key the server registered a waiter under (see
        # atlas.grpc_server.registry), not something this side computes.
        try:
            self.runtime.execute_task(task_id)
            result = atlas_pb2.TaskResult(
                task_id=str(task_id), attempt=dispatch_msg.attempt, skipped=False
            )
        except _SKIPPABLE_ERRORS as exc:
            result = atlas_pb2.TaskResult(
                task_id=str(task_id), attempt=dispatch_msg.attempt, skipped=True, error=str(exc)
            )
        self._outgoing.put(
            atlas_pb2.WorkerMessage(worker_id=str(self.runtime.worker.id), result=result)
        )

    def _start_heartbeat(self, interval_seconds: Optional[float] = None) -> None:
        from atlas.config import get_settings

        interval_seconds = (
            interval_seconds if interval_seconds is not None else get_settings().heartbeat_interval
        )
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, args=(interval_seconds,), daemon=True
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        if self._heartbeat_thread is None:
            return
        self._heartbeat_stop.set()
        self._heartbeat_thread.join(timeout=5)
        self._heartbeat_thread = None

    def _heartbeat_loop(self, interval_seconds: float) -> None:
        while True:
            timestamp = datetime.now(timezone.utc)
            try:
                self._stub.Heartbeat(
                    atlas_pb2.HeartbeatRequest(
                        worker_id=str(self.runtime.worker.id), timestamp=timestamp.isoformat()
                    )
                )
                # Mirror WorkerRuntime.send_heartbeat()'s local-path behavior
                # (Phase 04/07): keep this process's own Worker object's
                # last_heartbeat in sync with what was just sent over the
                # wire. Without this, WorkerRuntime._assign()/_finish()'s
                # own WorkerRepository.update() calls -- which write this
                # exact object as a full row, last_heartbeat included --
                # would overwrite the DB's real heartbeat with this
                # object's stale None the moment the worker claims its
                # first task, making it look instantly dead to
                # FailureDetector.
                self.runtime.worker.last_heartbeat = timestamp
            except grpc.RpcError:
                logger.exception("Heartbeat RPC failed for worker %s", self.runtime.worker.id)
            if self._heartbeat_stop.wait(interval_seconds):
                break
