"""GET /metrics integration tests against real PostgreSQL.

Skipped automatically when PostgreSQL is not reachable.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from atlas.domain import Job, JobStatus, Task
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow
from atlas.persistence.repositories import JobRepository, TaskRepository


def _postgres_reachable() -> bool:
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(), reason="PostgreSQL is not reachable"
)


@pytest.fixture
def client():
    from atlas.main import app

    return TestClient(app)


def test_metrics_returns_accurate_counts_against_live_db(client):
    job = Job(name="metrics-test-job", status=JobStatus.SUBMITTED)
    task = Task(job_id=job.id, type="shell", payload={})
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)

    try:
        before = client.get("/metrics").json()

        response = client.get("/metrics")
        assert response.status_code == 200
        body = response.json()

        assert body["jobs"]["SUBMITTED"] == before["jobs"]["SUBMITTED"]
        assert body["tasks"]["PENDING"] == before["tasks"]["PENDING"]
        assert "jobs" in body and "tasks" in body and "workers" in body

        # Re-fetch after deleting our fixture row should show the count drop
        with session_scope() as session:
            session.query(TaskRow).filter(TaskRow.job_id == job.id).delete()
            session.delete(session.get(JobRow, job.id))

        after = client.get("/metrics").json()
        assert after["jobs"]["SUBMITTED"] == before["jobs"]["SUBMITTED"] - 1
        assert after["tasks"]["PENDING"] == before["tasks"]["PENDING"] - 1
    finally:
        with session_scope() as session:
            session.query(TaskRow).filter(TaskRow.job_id == job.id).delete()
            job_row = session.get(JobRow, job.id)
            if job_row:
                session.delete(job_row)


def test_metrics_includes_all_status_keys(client):
    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.json()
    assert set(body["jobs"].keys()) == {
        "SUBMITTED",
        "QUEUED",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
    assert set(body["tasks"].keys()) == {
        "PENDING",
        "ASSIGNED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "RETRYING",
        "CANCELLED",
    }
    assert set(body["workers"].keys()) == {
        "REGISTERING",
        "AVAILABLE",
        "BUSY",
        "UNHEALTHY",
        "DEAD",
        "DRAINING",
    }
