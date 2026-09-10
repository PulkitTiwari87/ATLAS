"""Domain <-> ORM row mapping round-trip tests. No database needed."""

import uuid

from atlas.domain import Job, JobStatus, Task, TaskStatus, Worker, WorkerStatus
from atlas.persistence.repositories import (
    _job_to_domain,
    _job_to_row,
    _task_to_domain,
    _task_to_row,
    _worker_to_domain,
    _worker_to_row,
)


def test_job_round_trip():
    job = Job(name="demo", priority=5, status=JobStatus.QUEUED)

    round_tripped = _job_to_domain(_job_to_row(job))

    assert round_tripped == Job(
        name=job.name,
        priority=job.priority,
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def test_task_round_trip():
    task = Task(
        job_id=uuid.uuid4(),
        type="shell",
        payload={"cmd": "echo hi"},
        max_retries=5,
        status=TaskStatus.ASSIGNED,
        attempt=1,
        worker_id=uuid.uuid4(),
    )

    round_tripped = _task_to_domain(_task_to_row(task))

    assert round_tripped == task


def test_worker_round_trip():
    worker = Worker(
        hostname="host-1",
        address="10.0.0.1:9000",
        status=WorkerStatus.AVAILABLE,
    )

    round_tripped = _worker_to_domain(_worker_to_row(worker))

    assert round_tripped == worker
