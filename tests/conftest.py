"""Shared fixtures.

By default the tests run on a temporary SQLite file. Set MQI_TEST_DATABASE_URL to a PostgreSQL server
(e.g. postgresql://mqi:mqi@localhost:5432/postgres) to run the same tests on PostgreSQL: each fixture
then gets its own database on that server.
"""
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PG_ADMIN = os.environ.get("MQI_TEST_DATABASE_URL")
_created: list[str] = []


def _pg_url(name: str) -> str:
    base, _, _ = PG_ADMIN.rpartition("/")
    return f"{base}/{name}"


def _pg_create(name: str, template: str | None = None) -> str:
    import psycopg

    with psycopg.connect(PG_ADMIN, autocommit=True) as con:
        con.execute(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ""))
    _created.append(name)
    return _pg_url(name)


def new_store(tmp_path: Path):
    """An empty store for one test."""
    if PG_ADMIN:
        return _pg_create(f"mqi_t_{uuid.uuid4().hex[:10]}")
    return tmp_path / "mqi.sqlite"


def clone_store(src, tmp_path: Path):
    """A private copy of a store, so a test can write to it."""
    if PG_ADMIN:
        return _pg_create(f"mqi_t_{uuid.uuid4().hex[:10]}", template=str(src).rpartition("/")[2])
    dst = tmp_path / "mqi.sqlite"
    shutil.copy(src, dst)
    return dst


def pytest_sessionfinish(session, exitstatus):
    if not PG_ADMIN or not _created:
        return
    import psycopg

    with psycopg.connect(PG_ADMIN, autocommit=True) as con:
        for name in _created:
            con.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """Build the synthetic plant once into a temp folder and store."""
    from mqi import demo as d

    base = tmp_path_factory.mktemp("demo")
    folder, dbpath = base / "inbox", new_store(base)
    results = d.build(folder, dbpath)
    return {"folder": folder, "db": dbpath, "results": results}


@pytest.fixture(scope="session")
def bundle(demo):
    from mqi import engine

    return engine.run(demo["db"])
