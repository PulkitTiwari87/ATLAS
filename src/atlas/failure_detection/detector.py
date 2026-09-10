"""FailureDetector: interprets missing heartbeats as AVAILABLE/BUSY -> UNHEALTHY.

Detection only. Does not touch Task state, does not recover anything, does
not move a worker to DEAD — see plans/phase-08-failure-detection.md.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List

from atlas.config import get_settings
from atlas.domain import InvalidTransitionError, Worker, WorkerStatus
from atlas.observability import log_event
from atlas.persistence.db import session_scope
from atlas.persistence.repositories import WorkerRepository

logger = logging.getLogger("atlas.failure_detection")

_CANDIDATE_STATUSES = (WorkerStatus.AVAILABLE, WorkerStatus.BUSY)


class FailureDetector:
    def __init__(self):
        self._workers = WorkerRepository()
        self._stopping = False

    def run_cycle(self) -> None:
        for worker in self._load_candidates():
            if self._is_stale(worker):
                self._mark_unhealthy(worker.id)

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                self.run_cycle()
            except Exception:
                logger.exception("Failure detection cycle failed")
            if self._stopping:
                break
            time.sleep(poll_interval_seconds)

    def _load_candidates(self) -> List[Worker]:
        with session_scope() as session:
            workers = []
            for status in _CANDIDATE_STATUSES:
                workers.extend(self._workers.list_by_status(session, status))
        return workers

    def _is_stale(self, worker: Worker) -> bool:
        if worker.last_heartbeat is None:
            return True
        threshold = timedelta(seconds=get_settings().worker_timeout)
        return datetime.now(timezone.utc) - worker.last_heartbeat > threshold

    def _mark_unhealthy(self, worker_id) -> None:
        with session_scope() as session:
            worker = self._workers.get(session, worker_id)
            if worker is None or worker.status not in _CANDIDATE_STATUSES:
                return
            # Re-validate staleness against this fresh read, not the one
            # run_cycle() made moments ago: a heartbeat can land in the
            # gap between that scan and this commit (most easily hit
            # right after registration, when last_heartbeat is briefly
            # None), and without this check the worker would be marked
            # UNHEALTHY based on a snapshot that's already been corrected.
            if not self._is_stale(worker):
                return
            try:
                worker.transition_to(WorkerStatus.UNHEALTHY)
            except InvalidTransitionError:
                logger.info("Worker %s no longer eligible for UNHEALTHY", worker_id)
                return
            self._workers.update(session, worker)
        seconds_since_heartbeat = (
            (datetime.now(timezone.utc) - worker.last_heartbeat).total_seconds()
            if worker.last_heartbeat is not None
            else None
        )
        log_event(
            logger,
            "worker.unhealthy",
            level="warning",
            worker_id=worker_id,
            seconds_since_heartbeat=seconds_since_heartbeat,
        )
