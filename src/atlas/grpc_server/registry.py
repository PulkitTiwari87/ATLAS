"""GrpcWorkerRegistry: the gRPC-mode replacement for Phase 06's in-process
WorkerRegistry. Same conceptual shape (worker_id -> "something Dispatcher
can call execute_task(task_id) on") but backed by a live gRPC stream instead
of a local WorkerRuntime object.

Dispatcher itself needs zero changes: .get(worker_id) still returns an
object with a matching execute_task(task_id) method (RemoteWorkerHandle) —
see plans/phase-10-grpc.md section 14.

Result correlation is keyed by (task_id, attempt), not task_id alone. A
task can be dispatched more than once over its lifetime (e.g. Phase 09
recovers it from a dead worker and it is redispatched to a different
worker) — without the attempt in the key, a late TaskResult from an
earlier, abandoned execution could satisfy the waiter for a newer
execution of the same task_id. Task.attempt (Phase 01/04, incremented
once per RUNNING entry by WorkerRuntime._start(), read but never modified
by Phase 09 recovery) is the existing execution identity already in the
domain model — reused here rather than inventing a second one.
"""

import queue
import threading
import uuid
from typing import Dict, Optional, Tuple

from atlas.grpc_server import atlas_pb2
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import TaskRepository
from atlas.worker.errors import TaskNotFoundError, WorkerDrainingError

_RESULT_TIMEOUT_SECONDS = 310  # a little over WorkerRuntime's own shell task timeout

_WaiterKey = Tuple[str, int]  # (task_id, attempt)


class RemoteWorkerHandle:
    """Duck-types WorkerRuntime.execute_task(task_id) for Dispatcher."""

    def __init__(self, worker_id: uuid.UUID, outbox: "queue.Queue", registry: "GrpcWorkerRegistry"):
        self._worker_id = worker_id
        self._outbox = outbox
        self._registry = registry
        self._tasks = TaskRepository()

    def execute_task(self, task_id: uuid.UUID):
        # The attempt this dispatch will produce is computed *before*
        # sending it — the worker's own _assign()/_start() (unchanged,
        # Phase 04) will independently arrive at the same number, because
        # only one execution can ever claim a PENDING task at a time
        # (transition_to's existing InvalidTransitionError guard). Reading
        # it here, once, is what lets the result be correlated to *this*
        # specific execution rather than just this task.
        expected_attempt = self._next_attempt(task_id)

        waiter: "queue.Queue" = queue.Queue(maxsize=1)
        key: _WaiterKey = (str(task_id), expected_attempt)
        self._registry._register_waiter(key, waiter)
        try:
            self._outbox.put(
                atlas_pb2.ServerMessage(
                    dispatch=atlas_pb2.DispatchTask(task_id=str(task_id), attempt=expected_attempt)
                )
            )
            try:
                result = waiter.get(timeout=_RESULT_TIMEOUT_SECONDS)
            except queue.Empty:
                raise WorkerDrainingError(
                    f"No response from worker {self._worker_id} for task {task_id} "
                    f"attempt {expected_attempt}"
                )
        finally:
            self._registry._unregister_waiter(key)

        if result.skipped:
            raise WorkerDrainingError(result.error or "dispatch skipped by worker")

    def _next_attempt(self, task_id: uuid.UUID) -> int:
        with session_scope() as session:
            task = self._tasks.get(session, task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task.attempt + 1


class GrpcWorkerRegistry:
    def __init__(self):
        self._outboxes: Dict[uuid.UUID, "queue.Queue"] = {}
        self._waiters: Dict[_WaiterKey, "queue.Queue"] = {}
        self._lock = threading.Lock()

    def register(self, worker_id: uuid.UUID, outbox: "queue.Queue") -> None:
        with self._lock:
            self._outboxes[worker_id] = outbox

    def unregister(self, worker_id: uuid.UUID) -> None:
        with self._lock:
            self._outboxes.pop(worker_id, None)

    def get(self, worker_id: uuid.UUID) -> Optional[RemoteWorkerHandle]:
        with self._lock:
            outbox = self._outboxes.get(worker_id)
        if outbox is None:
            return None
        return RemoteWorkerHandle(worker_id, outbox, self)

    def deliver_result(self, result: atlas_pb2.TaskResult) -> None:
        key: _WaiterKey = (result.task_id, result.attempt)
        with self._lock:
            waiter = self._waiters.get(key)
        if waiter is not None:
            waiter.put(result)
        # else: no waiter registered for this (task_id, attempt) — either
        # it already timed out, or this is a late result from an earlier,
        # already-superseded execution. Dropped silently, by design: the
        # result is purely informational (section 6); real state was
        # already persisted, or not, by the worker's own WorkerRuntime.

    def _register_waiter(self, key: _WaiterKey, waiter: "queue.Queue") -> None:
        with self._lock:
            self._waiters[key] = waiter

    def _unregister_waiter(self, key: _WaiterKey) -> None:
        with self._lock:
            self._waiters.pop(key, None)
