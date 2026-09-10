"""DispatchLoop: minimal composition of Scheduler + Dispatcher.

Neither Scheduler nor Dispatcher depends on the other directly — this is
the only piece that knows about both (plans/phase-06-dispatch.md section 14).
"""

import logging
import time

from atlas.dispatch.dispatcher import Dispatcher
from atlas.scheduler import Scheduler

logger = logging.getLogger("atlas.dispatch")


class DispatchLoop:
    def __init__(self, scheduler: Scheduler, dispatcher: Dispatcher):
        self._scheduler = scheduler
        self._dispatcher = dispatcher
        self._stopping = False

    def run_cycle(self):
        proposals = self._scheduler.run_cycle()
        return self._dispatcher.dispatch_all(proposals)

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                self.run_cycle()
            except Exception:
                logger.exception("Dispatch cycle failed")
            if self._stopping:
                break
            time.sleep(poll_interval_seconds)
