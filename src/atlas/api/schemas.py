"""Pydantic request/response schemas for the Job API."""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from atlas.domain import JobStatus, TaskStatus


class TaskCreate(BaseModel):
    type: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)


class JobCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    priority: int = Field(default=0, ge=0)
    tasks: List[TaskCreate] = Field(min_length=1)


class TaskResponse(BaseModel):
    id: uuid.UUID
    type: str
    payload: dict
    status: TaskStatus
    attempt: int
    max_retries: int
    worker_id: Optional[uuid.UUID]


class JobResponse(BaseModel):
    id: uuid.UUID
    name: str
    priority: int
    status: JobStatus
    created_at: datetime
    updated_at: datetime


class JobDetailResponse(JobResponse):
    tasks: List[TaskResponse]
