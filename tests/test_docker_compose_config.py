"""Regression test for docker-compose.yml's Postgres healthcheck.

pg_isready with no -d flag defaults to a database named after the -U user
(libpq default), not POSTGRES_DB -- here that's "atlas" vs. the real
database "atlas_db", so an unqualified healthcheck repeatedly logs
`FATAL: database "atlas" does not exist` on the Postgres side even though
the server is healthy. No database access needed; this only parses the
compose file.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _postgres_healthcheck_test():
    config = yaml.safe_load(_COMPOSE_PATH.read_text())
    return config["services"]["postgres"]["healthcheck"]["test"]


def test_postgres_healthcheck_targets_the_real_database():
    test_cmd = _postgres_healthcheck_test()

    assert "pg_isready" in test_cmd
    assert "-d" in test_cmd, "pg_isready must be given -d explicitly, or it defaults to a database named after -U"
    db_arg_index = test_cmd.index("-d") + 1
    assert test_cmd[db_arg_index] == "atlas_db"
