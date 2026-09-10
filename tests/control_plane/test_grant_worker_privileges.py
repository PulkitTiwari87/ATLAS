"""Regression tests for atlas.control_plane.main._grant_worker_privileges.

Real PostgreSQL only -- skipped automatically when unreachable. Proves the
grant step executes docker/grant-worker-privileges.sql as-is (comments and
all) without a syntax error, and degrades gracefully when the atlas_worker
role isn't present (non-Docker/local dev).
"""

import pytest
from sqlalchemy import text

from atlas.control_plane.main import _grant_worker_privileges
from atlas.persistence.db import _engine


def _postgres_reachable() -> bool:
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(), reason="PostgreSQL is not reachable"
)


def _worker_role_exists() -> bool:
    with _engine.connect() as conn:
        return conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'atlas_worker'")
        ).first() is not None


def _worker_table_privileges() -> set:
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'atlas_worker'"
            )
        ).all()
    return {(row[0], row[1]) for row in rows}


@pytest.mark.skipif(not _worker_role_exists(), reason="atlas_worker role not present")
def test_grants_apply_without_a_syntax_error():
    with _engine.begin() as conn:
        conn.execute(text("REVOKE ALL ON workers, tasks, task_attempts FROM atlas_worker"))

    _grant_worker_privileges()  # must not raise

    privileges = _worker_table_privileges()
    assert ("workers", "SELECT") in privileges
    assert ("workers", "INSERT") in privileges
    assert ("workers", "UPDATE") in privileges
    assert ("tasks", "SELECT") in privileges
    assert ("tasks", "UPDATE") in privileges
    assert ("task_attempts", "INSERT") in privileges
    # Least privilege preserved: no DELETE anywhere, nothing on jobs.
    assert not any(priv == "DELETE" for _, priv in privileges)
    assert not any(table == "jobs" for table, _ in privileges)


def test_missing_role_is_reported_as_role_not_present_not_a_syntax_error(monkeypatch, caplog):
    # Simulate the non-Docker/local-dev case without touching the real
    # role: point the grant step at a definitely-nonexistent role name so
    # its ProgrammingError is guaranteed to be "role does not exist", not
    # some other failure this test would misinterpret as passing.
    import atlas.control_plane.main as control_plane_main

    monkeypatch.setattr(
        control_plane_main,
        "_GRANT_SQL_PATH",
        control_plane_main._GRANT_SQL_PATH.parent / "grant-worker-privileges.sql",
    )
    with _engine.connect() as conn:
        role_exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'atlas_worker_does_not_exist'")
        ).first()
    assert role_exists is None

    sql = control_plane_main._GRANT_SQL_PATH.read_text().replace(
        "atlas_worker", "atlas_worker_does_not_exist"
    )
    tmp_path = control_plane_main._GRANT_SQL_PATH.parent / "_test_grant_missing_role.sql"
    tmp_path.write_text(sql)
    monkeypatch.setattr(control_plane_main, "_GRANT_SQL_PATH", tmp_path)
    try:
        with caplog.at_level("WARNING"):
            _grant_worker_privileges()  # must not raise
        messages = " ".join(caplog.messages)
        assert "role not present" in messages
        assert "syntax error" not in messages
    finally:
        tmp_path.unlink(missing_ok=True)
