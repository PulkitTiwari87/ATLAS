"""Job domain model and its state machine."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from atlas.domain._transitions import ensure_transition_allowed
from atlas.domain.enums import JobStatus

_ALLOWED_TRANSITIONS = {
    JobStatus.SUBMITTED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


@dataclass
class Job:
    name: str
    priority: int = 0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: JobStatus = JobStatus.SUBMITTED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(self, target: JobStatus) -> None:
        ensure_transition_allowed("Job", self.status, target, _ALLOWED_TRANSITIONS)
        self.status = target
        self.updated_at = datetime.now(timezone.utc)
