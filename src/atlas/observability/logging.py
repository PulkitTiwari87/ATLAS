"""Structured logging helper for Atlas components.

log_event() emits one line per significant event with a consistent
event name plus key=value fields (job_id, task_id, worker_id, attempt,
...) instead of free-text messages — see plans/phase-11-observability.md.

A plain formatted string is used rather than logging's `extra=` dict:
`extra` keys that collide with reserved LogRecord attribute names (e.g.
"message") raise, which would defeat the "must never raise into calling
code" requirement below in the one case it matters most.
"""

import logging
import sys

_VALID_LEVELS = ("debug", "info", "warning", "error", "exception")


def log_event(logger: logging.Logger, event: str, level: str = "info", **fields) -> None:
    """Emit one structured log line. Never raises into calling code."""
    try:
        message = event
        if fields:
            message += " " + " ".join(f"{key}={value!r}" for key, value in fields.items())
        log_method = getattr(logger, level, None)
        if log_method is None or level not in _VALID_LEVELS:
            logger.info(message)
        else:
            log_method(message)
    except Exception:
        try:
            print(f"log_event failed: event={event} fields={fields}", file=sys.stderr)
        except Exception:
            pass
