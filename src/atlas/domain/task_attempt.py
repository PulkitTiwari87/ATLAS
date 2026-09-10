"""TaskAttempt domain model: a record of one execution attempt of a Task.

Unlike Job/Task/Worker, a TaskAttempt has no state machine of its own — it
is an immutable-in-spirit record of what happened during one attempt.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TaskAttempt:
    task_id: uuid.UUID
    worker_id: uuid.UUID
    attempt_number: int
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[dict] = None
    error: Optional[str] = None
