"""In-process worker lookup: worker_id -> WorkerRuntime.

Temporary Phase 06 mechanism (see plans/phase-06-dispatch.md section 15).
Phase 10 replaces this with something that resolves worker.address to a
gRPC channel/stub instead — Dispatcher.dispatch() does not change shape.
"""

from typing import Dict, Optional
from uuid import UUID

from atlas.worker import WorkerRuntime


class WorkerRegistry:
    def __init__(self):
        self._runtimes: Dict[UUID, WorkerRuntime] = {}

    def register(self, runtime: WorkerRuntime) -> None:
        self._runtimes[runtime.worker.id] = runtime

    def unregister(self, worker_id: UUID) -> None:
        self._runtimes.pop(worker_id, None)

    def get(self, worker_id: UUID) -> Optional[WorkerRuntime]:
        return self._runtimes.get(worker_id)
