"""Configuration validation unit tests. No database."""

import pytest

from atlas.config import ConfigurationError, get_settings


def test_defaults_are_valid():
    settings = get_settings()  # must not raise

    assert settings.heartbeat_interval > 0
    assert settings.worker_timeout > 0
    assert settings.max_retries >= 0


def test_valid_override_is_accepted(monkeypatch):
    monkeypatch.setenv("WORKER_TIMEOUT", "45")

    settings = get_settings()

    assert settings.worker_timeout == 45


@pytest.mark.parametrize(
    "env_var,value",
    [
        ("ATLAS_PORT", "0"),
        ("ATLAS_PORT", "70000"),
        ("GRPC_PORT", "0"),
        ("GRPC_PORT", "-1"),
        ("HEARTBEAT_INTERVAL", "0"),
        ("HEARTBEAT_INTERVAL", "-5"),
        ("WORKER_TIMEOUT", "0"),
        ("WORKER_TIMEOUT", "-30"),
        ("MAX_RETRIES", "-1"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch, env_var, value):
    monkeypatch.setenv(env_var, value)

    with pytest.raises(ConfigurationError):
        get_settings()


def test_zero_max_retries_is_valid(monkeypatch):
    """MAX_RETRIES=0 is a legitimate 'no retries' configuration — must not
    be rejected."""
    monkeypatch.setenv("MAX_RETRIES", "0")

    settings = get_settings()

    assert settings.max_retries == 0


def test_heartbeat_interval_must_be_less_than_worker_timeout(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_INTERVAL", "30")
    monkeypatch.setenv("WORKER_TIMEOUT", "30")

    with pytest.raises(ConfigurationError):
        get_settings()


def test_boundary_port_values_are_valid(monkeypatch):
    monkeypatch.setenv("ATLAS_PORT", "1")
    monkeypatch.setenv("GRPC_PORT", "65535")

    settings = get_settings()

    assert settings.port == 1
    assert settings.grpc_port == 65535
