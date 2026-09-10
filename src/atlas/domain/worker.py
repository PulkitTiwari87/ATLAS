"""Worker domain model and its state machine."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from atlas.domain._transitions import ensure_transition_allowed
from atlas.domain.enums import WorkerStatus

_ALLOWED_TRANSITIONS = {
    WorkerStatus.REGISTERING: {WorkerStatus.AVAILABLE},
    WorkerStatus.AVAILABLE: {
        WorkerStatus.BUSY,
        WorkerStatus.DRAINING,
        WorkerStatus.UNHEALTHY,
    },
    WorkerStatus.BUSY: {WorkerStatus.AVAILABLE, WorkerStatus.UNHEALTHY},
    WorkerStatus.DRAINING: {WorkerStatus.DEAD},
    WorkerStatus.UNHEALTHY: {WorkerStatus.DEAD},
    WorkerStatus.DEAD: set(),
}


@dataclass
class Worker:
    hostname: str
    address: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: WorkerStatus = WorkerStatus.REGISTERING
    last_heartbeat: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(self, target: WorkerStatus) -> None:
        ensure_transition_allowed("Worker", self.status, target, _ALLOWED_TRANSITIONS)
        self.status = target
