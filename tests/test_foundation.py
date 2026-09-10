"""Phase 00 foundation tests: package import, configuration, logging."""

import logging

from atlas.config import get_settings
from atlas.logging import setup_logging


def test_package_imports():
    import atlas

    assert atlas is not None


def test_settings_defaults():
    settings = get_settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.max_retries == 3


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("ATLAS_PORT", "9000")

    settings = get_settings()

    assert settings.port == 9000


def test_setup_logging_returns_configured_logger():
    logger = setup_logging("debug")

    assert logger.name == "atlas"
    assert logging.getLogger().level == logging.DEBUG
