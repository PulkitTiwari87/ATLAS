"""Task domain model and its state machine."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from atlas.domain._transitions import ensure_transition_allowed
from atlas.domain.enums import TaskStatus

_ALLOWED_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.RETRYING},
    TaskStatus.RETRYING: {TaskStatus.PENDING},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.CANCELLED: set(),
}


@dataclass
class Task:
    job_id: uuid.UUID
    type: str
    payload: dict = field(default_factory=dict)
    max_retries: int = 3
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: TaskStatus = TaskStatus.PENDING
    attempt: int = 0
    worker_id: Optional[uuid.UUID] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(self, target: TaskStatus) -> None:
        ensure_transition_allowed("Task", self.status, target, _ALLOWED_TRANSITIONS)
        self.status = target
        self.updated_at = datetime.now(timezone.utc)
