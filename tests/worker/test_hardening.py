"""Phase 13 hardening tests for the shell executor: payload validation
(item 2) and resource limits (item 3).

Unit tests need no database and always run. Integration tests (missing/
malformed payload producing a clean FAILED outcome end-to-end) reuse the
real WorkerRuntime against real PostgreSQL, same pattern as
tests/worker/test_task_execution.py, and are individually skipped when
Postgres is unreachable.
"""

import uuid

import pytest
from sqlalchemy import text

from atlas.domain import Job, Task, TaskStatus
from atlas.persistence.db import _engine, session_scope
from atlas.persistence.models import JobRow, TaskAttemptRow, TaskRow, WorkerRow
from atlas.persistence.repositories import JobRepository, TaskRepository
from atlas.worker import WorkerRuntime
from atlas.worker.runtime import _limit_shell_resources, _run_shell, resource


def _postgres_reachable() -> bool:
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_reachable(), reason="PostgreSQL is not reachable"
)


# --- Unit tests: no database ---


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing "command"
        {"command": None},
        {"command": 123},
        {"command": ""},
        {"command": "   "},
    ],
)
def test_run_shell_rejects_malformed_payload(payload):
    task = Task(job_id=uuid.uuid4(), type="shell", payload=payload)

    with pytest.raises(ValueError):
        _run_shell(task)


def test_run_shell_accepts_valid_command():
    task = Task(job_id=uuid.uuid4(), type="shell", payload={"command": "exit 0"})

    result = _run_shell(task)  # must not raise

    assert "stdout" in result


def test_limit_shell_resources_is_safe_noop_without_resource_module(monkeypatch):
    import atlas.worker.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "resource", None)

    runtime_module._limit_shell_resources()  # must not raise


@pytest.mark.skipif(resource is None, reason="resource module is POSIX-only")
def test_limit_shell_resources_sets_rlimits_on_posix():
    # Runs for real only on POSIX (Linux/Mac CI, Docker); skips on this
    # Windows dev environment where `resource` doesn't exist — see
    # module docstring and src/atlas/worker/runtime.py's platform note.
    #
    # _limit_shell_resources() is a preexec_fn: production only ever calls
    # it inside a forked child that's about to exec (subprocess.run), where
    # a lowered *hard* limit dies with that child. Calling it directly in
    # this test process would permanently lower this process's own hard
    # limits instead — irreversible without CAP_SYS_RESOURCE, and fatal to
    # every later test in the same pytest run once RLIMIT_AS is capped at
    # 512MB for the rest of the suite. Exercise it the same way production
    # does: inside an actual forked child, which discards the change on exit.
    import os

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        import resource as _resource

        try:
            _limit_shell_resources()
            cpu_limit = _resource.getrlimit(_resource.RLIMIT_CPU)
            as_limit = _resource.getrlimit(_resource.RLIMIT_AS)
            ok = cpu_limit[0] == 300 and as_limit[0] == 512 * 1024 * 1024
        except Exception:
            ok = False
        os.write(write_fd, b"1" if ok else b"0")
        os._exit(0)

    os.close(write_fd)
    result = os.read(read_fd, 1)
    os.close(read_fd)
    os.waitpid(pid, 0)
    assert result == b"1"


# --- Integration tests: real Postgres, real WorkerRuntime ---


def _make_job_and_task(payload):
    job = Job(name="hardening-test-job")
    task = Task(job_id=job.id, type="shell", payload=payload)
    with session_scope() as session:
        JobRepository().add(session, job)
        TaskRepository().add(session, task)
    return job, task


def _cleanup(job_id=None, worker_id=None):
    with session_scope() as session:
        if job_id:
            for row in session.query(TaskAttemptRow).join(
                TaskRow, TaskAttemptRow.task_id == TaskRow.id
            ).filter(TaskRow.job_id == job_id):
                session.delete(row)
            for row in session.query(TaskRow).filter(TaskRow.job_id == job_id):
                session.delete(row)
            job_row = session.get(JobRow, job_id)
            if job_row:
                session.delete(job_row)
        if worker_id:
            worker_row = session.get(WorkerRow, worker_id)
            if worker_row:
                session.delete(worker_row)


@requires_postgres
def test_missing_command_fails_task_cleanly_end_to_end():
    job, task = _make_job_and_task({})  # no "command" key at all
    runtime = WorkerRuntime()
    runtime.register()
    try:
        result = runtime.execute_task(task.id)

        assert result.status == TaskStatus.FAILED

        with session_scope() as session:
            attempts = list(
                session.query(TaskAttemptRow).filter(TaskAttemptRow.task_id == task.id)
            )
        assert len(attempts) == 1
        assert "command" in attempts[0].error
    finally:
        runtime.stop_heartbeat()
        _cleanup(job.id, runtime.worker.id)


@requires_postgres
def test_non_string_command_fails_task_cleanly_end_to_end():
    job, task = _make_job_and_task({"command": ["not", "a", "string"]})
    runtime = WorkerRuntime()
    runtime.register()
    try:
        result = runtime.execute_task(task.id)

        assert result.status == TaskStatus.FAILED
    finally:
        runtime.stop_heartbeat()
        _cleanup(job.id, runtime.worker.id)
