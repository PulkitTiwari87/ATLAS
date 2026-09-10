"""Job API routes. Thin: parse/validate, call JobService, map to HTTP."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from atlas.api.schemas import JobCreateRequest, JobDetailResponse, JobResponse, TaskResponse
from atlas.domain import Job, JobStatus, Task
from atlas.services import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])
_service = JobService()


def _task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        type=task.type,
        payload=task.payload,
        status=task.status,
        attempt=task.attempt,
        max_retries=task.max_retries,
        worker_id=task.worker_id,
    )


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        name=job.name,
        priority=job.priority,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _job_detail_response(job: Job, tasks: List[Task]) -> JobDetailResponse:
    return JobDetailResponse(
        **_job_response(job).model_dump(),
        tasks=[_task_response(t) for t in tasks],
    )


@router.post("", status_code=201, response_model=JobDetailResponse)
def create_job(request: JobCreateRequest) -> JobDetailResponse:
    job, tasks = _service.create_job(
        name=request.name,
        priority=request.priority,
        tasks=[(t.type, t.payload) for t in request.tasks],
    )
    return _job_detail_response(job, tasks)


@router.get("", response_model=List[JobResponse])
def list_jobs(status: Optional[JobStatus] = Query(default=None)) -> List[JobResponse]:
    jobs = _service.list_jobs(status=status)
    return [_job_response(job) for job in jobs]


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: uuid.UUID) -> JobDetailResponse:
    result = _service.get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    job, tasks = result
    return _job_detail_response(job, tasks)


@router.post("/{job_id}/cancel", response_model=JobDetailResponse)
def cancel_job(job_id: uuid.UUID) -> JobDetailResponse:
    job = _service.cancel_job(job_id)
    tasks = _service.get_job(job_id)[1]
    return _job_detail_response(job, tasks)
