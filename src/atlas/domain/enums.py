"""Status enums for Atlas domain entities."""

from enum import Enum


class JobStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class WorkerStatus(str, Enum):
    REGISTERING = "REGISTERING"
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    UNHEALTHY = "UNHEALTHY"
    DEAD = "DEAD"
    DRAINING = "DRAINING"
