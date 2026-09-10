"""Structured logging for Atlas — see plans/phase-11-observability.md.

Purely additive visibility: no component's decisions, return values, or
persisted state change as a result of anything in this package.
"""

from atlas.observability.logging import log_event

__all__ = ["log_event"]
