"""Worker runtime errors."""


class TaskNotFoundError(Exception):
    def __init__(self, task_id):
        super().__init__(f"Task {task_id} not found")
        self.task_id = task_id


class WorkerDrainingError(Exception):
    """Raised when execute_task is called after shutdown() has started."""
