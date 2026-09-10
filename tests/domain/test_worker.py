import pytest

from atlas.domain import InvalidTransitionError, Worker, WorkerStatus


def test_worker_defaults_to_registering():
    worker = Worker(hostname="host-1", address="10.0.0.1:9000")

    assert worker.status == WorkerStatus.REGISTERING


def test_valid_transition_chain():
    worker = Worker(hostname="host-1", address="10.0.0.1:9000")

    worker.transition_to(WorkerStatus.AVAILABLE)
    worker.transition_to(WorkerStatus.BUSY)
    worker.transition_to(WorkerStatus.AVAILABLE)
    worker.transition_to(WorkerStatus.DRAINING)
    worker.transition_to(WorkerStatus.DEAD)

    assert worker.status == WorkerStatus.DEAD


def test_unhealthy_to_dead():
    worker = Worker(
        hostname="host-1", address="10.0.0.1:9000", status=WorkerStatus.UNHEALTHY
    )

    worker.transition_to(WorkerStatus.DEAD)

    assert worker.status == WorkerStatus.DEAD


@pytest.mark.parametrize(
    "start,target",
    [
        (WorkerStatus.REGISTERING, WorkerStatus.BUSY),
        (WorkerStatus.DEAD, WorkerStatus.AVAILABLE),
        (WorkerStatus.DRAINING, WorkerStatus.AVAILABLE),
        (WorkerStatus.BUSY, WorkerStatus.DRAINING),
    ],
)
def test_invalid_transitions_are_rejected(start, target):
    worker = Worker(hostname="host-1", address="10.0.0.1:9000", status=start)

    with pytest.raises(InvalidTransitionError):
        worker.transition_to(target)

    assert worker.status == start
