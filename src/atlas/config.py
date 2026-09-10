"""Environment-based configuration for Atlas."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigurationError(ValueError):
    """Raised when environment-provided configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    host: str
    port: int
    grpc_port: int
    log_level: str
    heartbeat_interval: int
    worker_timeout: int
    max_retries: int


def get_settings() -> Settings:
    """Build Settings from environment variables, with defaults for local dev."""
    settings = Settings(
        database_url=os.getenv(
            "DATABASE_URL", "postgresql://atlas:atlas_pass@localhost:5432/atlas_db"
        ),
        host=os.getenv("ATLAS_HOST", "0.0.0.0"),
        port=int(os.getenv("ATLAS_PORT", "8000")),
        grpc_port=int(os.getenv("GRPC_PORT", "50051")),
        log_level=os.getenv("LOG_LEVEL", "info"),
        heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "5")),
        worker_timeout=int(os.getenv("WORKER_TIMEOUT", "30")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
    )
    _validate(settings)
    return settings


def _validate(settings: Settings) -> None:
    if not 1 <= settings.port <= 65535:
        raise ConfigurationError(f"ATLAS_PORT must be between 1 and 65535, got {settings.port}")
    if not 1 <= settings.grpc_port <= 65535:
        raise ConfigurationError(f"GRPC_PORT must be between 1 and 65535, got {settings.grpc_port}")
    if settings.heartbeat_interval <= 0:
        raise ConfigurationError(
            f"HEARTBEAT_INTERVAL must be positive, got {settings.heartbeat_interval}"
        )
    if settings.worker_timeout <= 0:
        raise ConfigurationError(f"WORKER_TIMEOUT must be positive, got {settings.worker_timeout}")
    if settings.max_retries < 0:
        raise ConfigurationError(f"MAX_RETRIES must be non-negative, got {settings.max_retries}")
    if settings.heartbeat_interval >= settings.worker_timeout:
        # A worker would always look stale to FailureDetector otherwise —
        # see plans/phase-08-failure-detection.md.
        raise ConfigurationError(
            f"HEARTBEAT_INTERVAL ({settings.heartbeat_interval}) must be less than "
            f"WORKER_TIMEOUT ({settings.worker_timeout})"
        )
