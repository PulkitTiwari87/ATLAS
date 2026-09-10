# REST API

## Overview

Atlas exposes a RESTful HTTP API for job submission, status queries, and control operations.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jobs` | Submit a new job. |
| `GET` | `/jobs` | List all jobs. |
| `GET` | `/jobs/{job_id}` | Retrieve job details and status. |
| `POST` | `/jobs/{job_id}/cancel` | Cancel a running or queued job. |
| `GET` | `/workers` | List all registered workers and their health status. |
| `GET` | `/workers/{worker_id}` | Get details for a specific worker. |
| `GET` | `/health` | Liveness probe. |
| `GET` | `/ready` | Readiness probe. |

### Request / Response Examples

**Submit Job**
```json
POST /jobs
Content-Type: application/json

{
  "name": "example-job",
  "priority": 10,
  "tasks": [{"type": "shell", "payload": {"command": "echo Hello"}}]
}
```

**Job Response**
```json
HTTP/1.1 201 Created
Content-Type: application/json

{
  "job_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "SUBMITTED"
}
```

All endpoints are defined as contracts only; implementation will be added in later phases.
