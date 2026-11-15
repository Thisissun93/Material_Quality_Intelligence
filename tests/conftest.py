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


@pytest.fixture()
def client(demo, tmp_path, monkeypatch):
    """API on a private copy of the demo store (SQLite file or PostgreSQL database)."""
    pytest.importorskip("fastapi")
    from mqi import config

    store = clone_store(demo["db"], tmp_path)
    if str(store).startswith("postgres"):
        monkeypatch.setenv("MQI_DATABASE_URL", str(store))
    else:
        monkeypatch.delenv("MQI_DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_PATH", store)
    from fastapi.testclient import TestClient

    from api import main

    main._cache.clear()
    c = TestClient(main.app)
    c.headers.update(login(c, "qa.lee"))           # most tests act as QA; see login() for other roles
    return c


def login(c, username: str, password: str = "demo1234") -> dict:
    r = c.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
