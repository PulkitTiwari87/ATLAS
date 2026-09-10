import pytest

from atlas.domain import InvalidTransitionError, Job, JobStatus


def test_job_defaults_to_submitted():
    job = Job(name="demo")

    assert job.status == JobStatus.SUBMITTED


def test_valid_transition_chain():
    job = Job(name="demo")

    job.transition_to(JobStatus.QUEUED)
    job.transition_to(JobStatus.RUNNING)
    job.transition_to(JobStatus.COMPLETED)

    assert job.status == JobStatus.COMPLETED


@pytest.mark.parametrize(
    "start,target",
    [
        (JobStatus.SUBMITTED, JobStatus.RUNNING),
        (JobStatus.SUBMITTED, JobStatus.COMPLETED),
        (JobStatus.QUEUED, JobStatus.COMPLETED),
        (JobStatus.COMPLETED, JobStatus.RUNNING),
        (JobStatus.CANCELLED, JobStatus.QUEUED),
    ],
)
def test_invalid_transitions_are_rejected(start, target):
    job = Job(name="demo", status=start)

    with pytest.raises(InvalidTransitionError):
        job.transition_to(target)

    assert job.status == start


def test_cancellation_from_submitted_and_queued():
    job = Job(name="demo")
    job.transition_to(JobStatus.CANCELLED)
    assert job.status == JobStatus.CANCELLED

    job2 = Job(name="demo")
    job2.transition_to(JobStatus.QUEUED)
    job2.transition_to(JobStatus.CANCELLED)
    assert job2.status == JobStatus.CANCELLED
