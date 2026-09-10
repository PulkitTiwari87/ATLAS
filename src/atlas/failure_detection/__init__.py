"""Failure detection: interprets missing heartbeats as AVAILABLE/BUSY ->
UNHEALTHY. Detection only — recovery is Phase 09's job.
"""

from atlas.failure_detection.detector import FailureDetector

__all__ = ["FailureDetector"]
