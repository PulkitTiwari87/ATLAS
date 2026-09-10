"""Dispatch: turns Scheduler proposals into WorkerRuntime.execute_task()
calls. Executes nothing itself, assigns nothing itself — see
plans/phase-06-dispatch.md.
"""

from atlas.dispatch.dispatcher import DispatchResult, Dispatcher
from atlas.dispatch.loop import DispatchLoop
from atlas.dispatch.registry import WorkerRegistry

__all__ = ["Dispatcher", "DispatchResult", "DispatchLoop", "WorkerRegistry"]
