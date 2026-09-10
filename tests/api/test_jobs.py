"""HTTP-level tests for the Job API (skips if PostgreSQL is unreachable)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskRow


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


@pytest.fixture
def created_job_ids():
    ids = []
    yield ids
    with session_scope() as session:
        for job_id in ids:
            for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
                session.delete(row)
            job_row = session.get(JobRow, job_id)
            if job_row:
                session.delete(job_row)


def _create_job(client, created_job_ids, **overrides):
    body = {
        "name": "api-job",
        "priority": 1,
        "tasks": [{"type": "shell", "payload": {"command": "echo hi"}}],
    }
    body.update(overrides)
    response = client.post("/jobs", json=body)
    created_job_ids.append(response.json()["id"])
    return response


def test_create_job_returns_201(client, created_job_ids):
    response = _create_job(client, created_job_ids)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "SUBMITTED"
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["status"] == "PENDING"
    assert body["tasks"][0]["payload"] == {"command": "echo hi"}


def test_create_job_requires_at_least_one_task(client, created_job_ids):
    response = client.post("/jobs", json={"name": "no-tasks", "tasks": []})

    assert response.status_code == 422


def test_create_job_rejects_negative_priority(client, created_job_ids):
    response = client.post(
        "/jobs",
        json={"name": "bad-priority", "priority": -1, "tasks": [{"type": "shell"}]},
    )

    assert response.status_code == 422


def test_get_job_returns_200(client, created_job_ids):
    created = _create_job(client, created_job_ids).json()

    response = client.get(f"/jobs/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_unknown_job_returns_404(client):
    response = client.get(f"/jobs/{uuid.uuid4()}")

    assert response.status_code == 404


def test_list_jobs_returns_created_job(client, created_job_ids):
    created = _create_job(client, created_job_ids).json()

    response = client.get("/jobs")

    assert response.status_code == 200
    assert created["id"] in [job["id"] for job in response.json()]


def test_list_jobs_filters_by_status(client, created_job_ids):
    created = _create_job(client, created_job_ids).json()

    submitted = client.get("/jobs", params={"status": "SUBMITTED"})
    running = client.get("/jobs", params={"status": "RUNNING"})

    assert created["id"] in [job["id"] for job in submitted.json()]
    assert created["id"] not in [job["id"] for job in running.json()]


def test_cancel_job_returns_200(client, created_job_ids):
    created = _create_job(client, created_job_ids).json()

    response = client.post(f"/jobs/{created['id']}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert all(t["status"] == "CANCELLED" for t in body["tasks"])


def test_cancel_unknown_job_returns_404(client):
    response = client.post(f"/jobs/{uuid.uuid4()}/cancel")

    assert response.status_code == 404


def test_cancel_already_cancelled_job_returns_409(client, created_job_ids):
    created = _create_job(client, created_job_ids).json()
    client.post(f"/jobs/{created['id']}/cancel")

    response = client.post(f"/jobs/{created['id']}/cancel")

    assert response.status_code == 409


def test_health_endpoint_still_works(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
