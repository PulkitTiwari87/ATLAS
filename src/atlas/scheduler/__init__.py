"""Scheduler: decides which runnable tasks should run next and moves
SUBMITTED jobs to QUEUED. Executes nothing, assigns nothing.
"""

from atlas.scheduler.scheduler import Scheduler

__all__ = ["Scheduler"]
