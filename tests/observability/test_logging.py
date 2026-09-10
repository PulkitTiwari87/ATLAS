"""log_event unit tests. No database."""

import logging

import pytest

from atlas.observability import log_event


def test_log_event_emits_event_name_and_fields(caplog):
    logger = logging.getLogger("test.observability")
    with caplog.at_level(logging.INFO, logger="test.observability"):
        log_event(logger, "task.claimed", task_id="abc", worker_id="def")

    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert "task.claimed" in message
    assert "task_id='abc'" in message
    assert "worker_id='def'" in message


def test_log_event_with_no_fields_just_logs_event_name(caplog):
    logger = logging.getLogger("test.observability")
    with caplog.at_level(logging.INFO, logger="test.observability"):
        log_event(logger, "scheduler.cycle")

    assert caplog.records[0].message == "scheduler.cycle"


def test_log_event_respects_level(caplog):
    logger = logging.getLogger("test.observability")
    with caplog.at_level(logging.WARNING, logger="test.observability"):
        log_event(logger, "worker.unhealthy", level="warning", worker_id="x")

    assert caplog.records[0].levelname == "WARNING"


def test_log_event_never_raises_on_bad_level():
    logger = logging.getLogger("test.observability")

    log_event(logger, "some.event", level="not_a_real_level", x=1)  # must not raise


def test_log_event_never_raises_when_logger_itself_is_broken():
    class _BrokenLogger:
        def info(self, *_args, **_kwargs):
            raise RuntimeError("logging backend down")

    log_event(_BrokenLogger(), "some.event", x=1)  # must not raise


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error"])
def test_log_event_supports_standard_levels(caplog, level):
    logger = logging.getLogger("test.observability")
    with caplog.at_level(logging.DEBUG, logger="test.observability"):
        log_event(logger, "some.event", level=level)

    assert len(caplog.records) == 1
