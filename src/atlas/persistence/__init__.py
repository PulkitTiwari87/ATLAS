"""PostgreSQL persistence layer: ORM models, sessions, and repositories.

Maps atlas.domain objects to/from the database. Domain code must never
import from this package (dependency points one way: persistence -> domain).
"""

from atlas.persistence.db import session_scope
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository

__all__ = [
    "session_scope",
    "JobRepository",
    "TaskRepository",
    "WorkerRepository",
]
