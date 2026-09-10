"""Atlas application entry point."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from atlas.api.routes.jobs import router as jobs_router
from atlas.config import get_settings
from atlas.domain import JobStatus, TaskStatus, WorkerStatus
from atlas.domain.errors import InvalidTransitionError
from atlas.logging import setup_logging
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository
from atlas.services import JobNotFoundError

settings = get_settings()
logger = setup_logging(settings.log_level)

app = FastAPI(title="Atlas")
app.include_router(jobs_router)


@app.exception_handler(JobNotFoundError)
def _job_not_found(request: Request, exc: JobNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(InvalidTransitionError)
def _invalid_transition(request: Request, exc: InvalidTransitionError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict:
    """Simple, DB-derived counts — no in-memory state to drift from
    PostgreSQL (the single source of truth, ADR-002). Not a Prometheus
    exporter; see plans/phase-11-observability.md section 6."""
    job_repo, task_repo, worker_repo = JobRepository(), TaskRepository(), WorkerRepository()
    with session_scope() as session:
        jobs = {status.value: len(job_repo.list_by_status(session, status)) for status in JobStatus}
        tasks = {status.value: len(task_repo.list_by_status(session, status)) for status in TaskStatus}
        workers = {
            status.value: len(worker_repo.list_by_status(session, status)) for status in WorkerStatus
        }
    return {"jobs": jobs, "tasks": tasks, "workers": workers}
