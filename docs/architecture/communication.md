# Communication

Atlas uses two communication protocols:

- **REST** – External client‑facing API for job submission, status queries, and control operations. Implemented with FastAPI.
- **gRPC** – Internal high‑performance protocol between the control plane and workers for task assignment, heartbeat, and result reporting.

No additional messaging layers are introduced at this stage.
