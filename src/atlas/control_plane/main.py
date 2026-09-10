"""Production control-plane process.

GrpcWorkerRegistry is in-process state (queues keyed by worker_id, live in
memory — see atlas.grpc_server.registry). The gRPC server and everything
that dispatches through the registry (Dispatcher, and therefore
DispatchLoop) must therefore share one instance in one process. This
module is that process: it owns the one GrpcWorkerRegistry, starts the
gRPC server on it, and starts DispatchLoop/FailureDetector/RecoveryManager
as background threads alongside the REST API (FastAPI/uvicorn, on the main
thread/event loop).

Startup order (see plans/ for each component's own contract):
  1. Run pending Alembic migrations (schema authority stays Alembic; a
     failure here aborts startup rather than serving against a stale
     schema).
  2. Grant the least-privilege atlas_worker role its table privileges
     (Phase 13) — safe to (re)run every startup: plain idempotent GRANTs,
     executed with the control plane's own (admin) DB credentials, so the
     worker's role is never widened to perform this step itself. Missing
     role (e.g. non-Docker/local dev without docker/postgres-init) is
     logged and skipped, not fatal.
  3. Build the shared GrpcWorkerRegistry and start the gRPC server on it.
  4. Start DispatchLoop, FailureDetector, RecoveryManager.
  5. Serve the REST API (blocks until shutdown).

Shutdown reverses this: stop the three loops, join their threads, then
stop the gRPC server -- no component is left running unattended.
"""

import logging
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from atlas.config import get_settings
from atlas.dispatch import Dispatcher, DispatchLoop
from atlas.failure_detection import FailureDetector
from atlas.grpc_server.registry import GrpcWorkerRegistry
from atlas.grpc_server.server import build_server
from atlas.logging import setup_logging
from atlas.main import create_app
from atlas.persistence.db import _engine
from atlas.recovery import RecoveryManager
from atlas.scheduler import Scheduler

logger = logging.getLogger("atlas.control_plane")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GRANT_SQL_PATH = _REPO_ROOT / "docker" / "grant-worker-privileges.sql"
_LOOP_POLL_INTERVAL_SECONDS = 1.0
_GRPC_SHUTDOWN_GRACE_SECONDS = 5.0
_THREAD_JOIN_TIMEOUT_SECONDS = 10.0


def _run_migrations() -> None:
    logger.info("Running database migrations (alembic upgrade head)")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Migration failed:\n%s", result.stdout + result.stderr)
        raise RuntimeError(f"alembic upgrade head failed (exit {result.returncode})")
    logger.info("Database schema is up to date")


def _grant_worker_privileges() -> None:
    """Best-effort: the atlas_worker role only exists when the Docker
    postgres-init script has run (see docker/postgres-init/01-worker-role.sql).
    A local/non-Docker deployment without that role is not an error."""
    if not _GRANT_SQL_PATH.exists():
        return
    # Hand the whole file to Postgres as one multi-statement script instead
    # of hand-parsing it: psycopg2 sends an unparameterized text() query via
    # the simple query protocol, which -- like `psql -f` -- lets Postgres's
    # own SQL parser split statements and strip comments. That's what
    # actually understands SQL comment syntax; a "does this line start with
    # --" filter doesn't (this file has a comment containing a literal ';',
    # which previously broke a split-on-";" version of this function).
    try:
        with _engine.begin() as conn:
            conn.execute(text(_GRANT_SQL_PATH.read_text()))
        logger.info("Granted atlas_worker table privileges")
    except ProgrammingError as exc:
        logger.warning("Skipping worker privilege grant (atlas_worker role not present): %s", exc)


def _start_loop(component) -> threading.Thread:
    thread = threading.Thread(
        target=component.run_forever, args=(_LOOP_POLL_INTERVAL_SECONDS,), daemon=True
    )
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()

    _run_migrations()
    _grant_worker_privileges()

    registry = GrpcWorkerRegistry()
    grpc_server = build_server(registry, port=settings.grpc_port)
    grpc_server.start()
    logger.info("gRPC server listening on :%d", settings.grpc_port)

    dispatch_loop = DispatchLoop(Scheduler(), Dispatcher(registry))
    failure_detector = FailureDetector()
    recovery_manager = RecoveryManager()
    components = (dispatch_loop, failure_detector, recovery_manager)
    threads = [_start_loop(component) for component in components]
    logger.info("DispatchLoop, FailureDetector and RecoveryManager started")

    app.state.registry = registry

    try:
        yield
    finally:
        logger.info("Shutting down control plane")
        for component in components:
            component.stop()
        for thread in threads:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
        grpc_server.stop(_GRPC_SHUTDOWN_GRACE_SECONDS).wait()
        logger.info("Control plane shutdown complete")


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = create_app(lifespan=lifespan)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
