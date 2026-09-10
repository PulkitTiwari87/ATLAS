"""Service-layer errors."""


class JobNotFoundError(Exception):
    def __init__(self, job_id):
        super().__init__(f"Job {job_id} not found")
        self.job_id = job_id
