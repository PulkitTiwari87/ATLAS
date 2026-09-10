"""Worker runtime: registers a worker and executes tasks assigned to it.

execute_task() is a direct in-process call today. It is the seam a future
Dispatcher (Phase 06) and, eventually, a gRPC handler (Phase 10) call into —
see plans/phase-04-worker-runtime.md section 6.
"""

import logging
import os
import socket
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from atlas.config import get_settings
from atlas.domain import Task, TaskAttempt, TaskStatus, Worker, WorkerStatus
from atlas.observability import log_event
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import TaskAttemptRepository, TaskRepository, WorkerRepository
from atlas.worker.errors import TaskNotFoundError, WorkerDrainingError

logger = logging.getLogger("atlas.worker")

_SHELL_TASK_TIMEOUT_SECONDS = 300

# POSIX-only resource caps for the shell subprocess (CPU time, address
# space). `resource` doesn't exist on Windows — on that platform
# subprocess.run's `timeout` above remains the only enforced bound; this
# is a real, documented platform limitation, not an oversight. Not a
# sandbox: it bounds one runaway process's own resource use, nothing more
# (no filesystem/network isolation) — see plans/phase-13-hardening.md
# item 1, which explicitly rules out containerization here.
try:
    import resource
except ImportError:  # Windows
    resource = None

_SHELL_MAX_CPU_SECONDS = _SHELL_TASK_TIMEOUT_SECONDS
_SHELL_MAX_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MB


def _limit_shell_resources() -> None:
    """Runs in the child process after fork(), before exec() — POSIX only
    (subprocess's `preexec_fn`). No-op if `resource` isn't available."""
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (_SHELL_MAX_CPU_SECONDS, _SHELL_MAX_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (_SHELL_MAX_MEMORY_BYTES, _SHELL_MAX_MEMORY_BYTES))


def _run_shell(task: Task) -> dict:
    command = task.payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(
            f"shell task requires a non-empty 'command' string in payload, got {command!r}"
        )
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=_SHELL_TASK_TIMEOUT_SECONDS,
        check=True,
        preexec_fn=_limit_shell_resources if resource is not None else None,
    )
    return {"stdout": completed.stdout, "stderr": completed.stderr}


_EXECUTORS: Dict[str, Callable[[Task], dict]] = {"shell": _run_shell}


class WorkerRuntime:
    def __init__(self):
        hostname = socket.gethostname()
        self.worker = Worker(hostname=hostname, address=f"{hostname}:{os.getpid()}")
        self._workers = WorkerRepository()
        self._tasks = TaskRepository()
        self._attempts = TaskAttemptRepository()
        self._draining = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()

    def register(self) -> Worker:
        with session_scope() as session:
            self._workers.add(session, self.worker)
        self.worker.transition_to(WorkerStatus.AVAILABLE)
        with session_scope() as session:
            self._workers.update(session, self.worker)
        self.start_heartbeat()
        return self.worker

    def execute_task(self, task_id: uuid.UUID) -> Task:
        if self._draining:
            raise WorkerDrainingError(f"Worker {self.worker.id} is draining")

        task = self._assign(task_id)
        task = self._start(task)

        started_at = datetime.now(timezone.utc)
        try:
            result = _EXECUTORS.get(task.type, _unknown_task_type)(task)
            error = None
        except Exception as exc:  # noqa: BLE001 - task code is arbitrary
            result = None
            error = str(exc)
        finished_at = datetime.now(timezone.utc)

        return self._finish(task, result=result, error=error, started_at=started_at, finished_at=finished_at)

    def shutdown(self) -> Worker:
        self.stop_heartbeat()
        self._draining = True
        self.worker.transition_to(WorkerStatus.DRAINING)
        self.worker.transition_to(WorkerStatus.DEAD)
        with session_scope() as session:
            self._workers.update(session, self.worker)
        return self.worker

    def send_heartbeat(self) -> None:
        timestamp = datetime.now(timezone.utc)
        with session_scope() as session:
            self._workers.update_heartbeat(session, self.worker.id, timestamp)
        # Keep the in-memory Worker in sync so a later full-row
        # WorkerRepository.update() (_assign/_finish/shutdown) doesn't
        # overwrite this heartbeat's DB write with a stale None/older value.
        self.worker.last_heartbeat = timestamp

    def start_heartbeat(self, interval_seconds: Optional[float] = None) -> None:
        if self._heartbeat_thread is not None:
            return
        interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else get_settings().heartbeat_interval
        )
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, args=(interval_seconds,), daemon=True
        )
        self._heartbeat_thread.start()
        log_event(logger, "worker.heartbeat_started", worker_id=self.worker.id)

    def stop_heartbeat(self) -> None:
        if self._heartbeat_thread is None:
            return
        self._heartbeat_stop.set()
        self._heartbeat_thread.join(timeout=5)
        self._heartbeat_thread = None
        log_event(logger, "worker.heartbeat_stopped", worker_id=self.worker.id)

    def _heartbeat_loop(self, interval_seconds: float) -> None:
        # Send first, then wait-with-early-exit — guarantees at least one
        # heartbeat lands even if stop_heartbeat() races in immediately
        # after start_heartbeat() (checking the stop flag before the first
        # send could otherwise skip it entirely).
        while True:
            try:
                self.send_heartbeat()
            except Exception:
                logger.exception("Heartbeat failed for worker %s", self.worker.id)
            if self._heartbeat_stop.wait(interval_seconds):
                break

    def _assign(self, task_id: uuid.UUID) -> Task:
        with session_scope() as session:
            task = self._tasks.get(session, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)

            task.transition_to(TaskStatus.ASSIGNED)
            task.worker_id = self.worker.id
            self._tasks.update(session, task)

            self.worker.transition_to(WorkerStatus.BUSY)
            self._workers.update(session, self.worker)
        log_event(logger, "task.claimed", task_id=task.id, worker_id=self.worker.id)
        return task

    def _start(self, task: Task) -> Task:
        with session_scope() as session:
            task.transition_to(TaskStatus.RUNNING)
            task.attempt += 1
            self._tasks.update(session, task)
        log_event(logger, "task.started", task_id=task.id, worker_id=self.worker.id, attempt=task.attempt)
        return task

    def _finish(self, task: Task, *, result, error, started_at, finished_at) -> Task:
        outcome = TaskStatus.FAILED if error else TaskStatus.SUCCEEDED
        with session_scope() as session:
            task.transition_to(outcome)
            self._tasks.update(session, task)

            self._attempts.add(
                session,
                TaskAttempt(
                    task_id=task.id,
                    worker_id=self.worker.id,
                    attempt_number=task.attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    result=result,
                    error=error,
                ),
            )

            self.worker.transition_to(WorkerStatus.AVAILABLE)
            self._workers.update(session, self.worker)
        log_event(
            logger,
            "task.succeeded" if outcome == TaskStatus.SUCCEEDED else "task.failed",
            task_id=task.id,
            worker_id=self.worker.id,
            attempt=task.attempt,
        )
        return task


def _unknown_task_type(task: Task) -> dict:
    raise ValueError(f"No executor registered for task type {task.type!r}")
