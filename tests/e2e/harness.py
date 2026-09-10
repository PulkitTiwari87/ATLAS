"""AtlasTestSystem: E2E test harness composing real Scheduler, Dispatcher,
FailureDetector, and RecoveryManager against real PostgreSQL, plus N real
WorkerRuntimes. See plans/phase-12-integration-e2e.md.

Local transport only (Phase 06's WorkerRegistry/Dispatcher) — the gRPC
transport (Phase 10) has its own dedicated suite in tests/grpc/ and isn't
needed to exercise any of these E2E scenarios.

Drives dispatch through a single DispatchLoop, which already calls
Scheduler.run_cycle() internally as its first step — running
Scheduler.run_forever() as a second, separate loop alongside it would
double-invoke the scheduler for no reason, so this harness starts only
DispatchLoop, FailureDetector, and RecoveryManager as background loops.
"""

import threading
import time
from typing import List, Optional, Tuple
from uuid import UUID

from atlas.dispatch import Dispatcher, DispatchLoop, WorkerRegistry
from atlas.domain import Job, JobStatus, Task, TaskStatus
from atlas.failure_detection import FailureDetector
from atlas.persistence.db import session_scope
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository, WorkerRepository
from atlas.recovery import RecoveryManager
from atlas.scheduler import Scheduler
from atlas.services import JobService
from atlas.worker import WorkerRuntime

_DEFAULT_POLL_INTERVAL = 0.05
_DEFAULT_TIMEOUT = 10.0


def cleanup_job(job_id: UUID) -> None:
    with session_scope() as session:
        for row in session.query(TaskAttemptRow).join(
            TaskRow, TaskAttemptRow.task_id == TaskRow.id
        ).filter(TaskRow.job_id == job_id):
            session.delete(row)
        for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
            session.delete(row)
        job_row = session.get(JobRow, job_id)
        if job_row:
            session.delete(job_row)


def cleanup_worker(worker_id: UUID) -> None:
    with session_scope() as session:
        worker_row = session.get(WorkerRow, worker_id)
        if worker_row:
            session.delete(worker_row)


class AtlasTestSystem:
    """Context manager: starts the control-plane loops and tracks
    everything it creates so tests don't need their own cleanup
    boilerplate. Not scenario-specific — reusable across the whole suite.
    """

    def __init__(self, poll_interval: float = _DEFAULT_POLL_INTERVAL):
        self._poll_interval = poll_interval
        self.registry = WorkerRegistry()
        self.dispatch_loop = DispatchLoop(Scheduler(), Dispatcher(self.registry))
        self.failure_detector = FailureDetector()
        self.recovery_manager = RecoveryManager()
        self.job_service = JobService()
        self._workers: List[WorkerRuntime] = []
        self._job_ids: List[UUID] = []
        self._threads = []

    def __enter__(self) -> "AtlasTestSystem":
        for component in (self.dispatch_loop, self.failure_detector, self.recovery_manager):
            self._start_loop(component)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.dispatch_loop.stop()
        self.failure_detector.stop()
        self.recovery_manager.stop()
        for thread in self._threads:
            thread.join(timeout=5)

        for worker in self._workers:
            try:
                worker.shutdown()
            except Exception:
                pass
            cleanup_worker(worker.worker.id)

        for job_id in self._job_ids:
            cleanup_job(job_id)

    def _start_loop(self, component) -> None:
        thread = threading.Thread(
            target=component.run_forever, args=(self._poll_interval,), daemon=True
        )
        thread.start()
        self._threads.append(thread)

    def add_worker(self, heartbeat_interval_seconds: Optional[float] = None) -> WorkerRuntime:
        runtime = WorkerRuntime()
        runtime.register()
        if heartbeat_interval_seconds is not None:
            # register() already started heartbeating at the configured
            # default (settings.heartbeat_interval, 5s) — restart at the
            # requested interval. Needed whenever a test uses an
            # artificially fast FailureDetector threshold: a worker whose
            # own heartbeat interval is slower than that threshold would
            # otherwise look "stale" between its own ticks even though
            # it's perfectly healthy.
            runtime.stop_heartbeat()
            runtime.start_heartbeat(interval_seconds=heartbeat_interval_seconds)

        # register() persists the worker with last_heartbeat=None before
        # its heartbeat thread's first tick lands. FailureDetector treats
        # a None heartbeat as immediately stale (Phase 08, by design) —
        # with this harness's fast poll interval, a concurrently-running
        # FailureDetector cycle can occasionally land in that narrow
        # window and mark a perfectly healthy, just-registered worker
        # UNHEALTHY before it ever gets to prove it's alive. Closing the
        # window here, once, makes every scenario that adds a worker
        # while loops are already running safe by construction.
        self._wait_for_first_heartbeat(runtime)

        self.registry.register(runtime)
        self._workers.append(runtime)
        return runtime

    def _wait_for_first_heartbeat(self, runtime: WorkerRuntime, timeout: float = _DEFAULT_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        worker_repo = WorkerRepository()
        while time.monotonic() < deadline:
            with session_scope() as session:
                fetched = worker_repo.get(session, runtime.worker.id)
            if fetched is not None and fetched.last_heartbeat is not None:
                return
            time.sleep(0.01)
        raise TimeoutError(f"Worker {runtime.worker.id} never sent a heartbeat within {timeout}s")

    def submit_job(
        self, name: str, priority: int, tasks: List[Tuple[str, dict]]
    ) -> Tuple[Job, List[Task]]:
        job, task_objects = self.job_service.create_job(name=name, priority=priority, tasks=tasks)
        self._job_ids.append(job.id)
        return job, task_objects

    def wait_for_task_status(
        self, task_id: UUID, status: TaskStatus, timeout: float = _DEFAULT_TIMEOUT
    ) -> Task:
        deadline = time.monotonic() + timeout
        tasks = TaskRepository()
        last = None
        while time.monotonic() < deadline:
            with session_scope() as session:
                last = tasks.get(session, task_id)
            if last is not None and last.status == status:
                return last
            time.sleep(0.02)
        raise TimeoutError(
            f"Task {task_id} did not reach {status} within {timeout}s (last={last})"
        )

    def wait_for_job_status(
        self, job_id: UUID, status: JobStatus, timeout: float = _DEFAULT_TIMEOUT
    ) -> Job:
        deadline = time.monotonic() + timeout
        jobs = JobRepository()
        last = None
        while time.monotonic() < deadline:
            with session_scope() as session:
                last = jobs.get(session, job_id)
            if last is not None and last.status == status:
                return last
            time.sleep(0.02)
        raise TimeoutError(
            f"Job {job_id} did not reach {status} within {timeout}s (last={last})"
        )
