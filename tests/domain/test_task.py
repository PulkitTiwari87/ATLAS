import uuid

import pytest

from atlas.domain import InvalidTransitionError, Task, TaskStatus


def test_task_defaults_to_pending():
    task = Task(job_id=uuid.uuid4(), type="shell")

    assert task.status == TaskStatus.PENDING


def test_valid_transition_chain_success():
    task = Task(job_id=uuid.uuid4(), type="shell")

    task.transition_to(TaskStatus.ASSIGNED)
    task.transition_to(TaskStatus.RUNNING)
    task.transition_to(TaskStatus.SUCCEEDED)

    assert task.status == TaskStatus.SUCCEEDED


def test_valid_retry_cycle():
    task = Task(job_id=uuid.uuid4(), type="shell")

    task.transition_to(TaskStatus.ASSIGNED)
    task.transition_to(TaskStatus.RUNNING)
    task.transition_to(TaskStatus.FAILED)
    task.transition_to(TaskStatus.RETRYING)
    task.transition_to(TaskStatus.PENDING)

    assert task.status == TaskStatus.PENDING


def test_assigned_can_fail_when_worker_dies_before_starting():
    """Phase 09: a task claimed (ASSIGNED) by a worker that dies before it
    ever reaches RUNNING must be recoverable via FAILED -> RETRYING ->
    PENDING, same as any other failure."""
    task = Task(job_id=uuid.uuid4(), type="shell", status=TaskStatus.ASSIGNED)

    task.transition_to(TaskStatus.FAILED)

    assert task.status == TaskStatus.FAILED


@pytest.mark.parametrize(
    "start,target",
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.SUCCEEDED),
        (TaskStatus.ASSIGNED, TaskStatus.SUCCEEDED),
        (TaskStatus.SUCCEEDED, TaskStatus.RETRYING),
        (TaskStatus.FAILED, TaskStatus.PENDING),
        (TaskStatus.CANCELLED, TaskStatus.PENDING),
    ],
)
def test_invalid_transitions_are_rejected(start, target):
    task = Task(job_id=uuid.uuid4(), type="shell", status=start)

    with pytest.raises(InvalidTransitionError):
        task.transition_to(target)

    assert task.status == start
