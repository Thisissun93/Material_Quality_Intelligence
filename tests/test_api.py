"""REST API: the same rules as the app, reachable over HTTP."""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(demo, tmp_path, monkeypatch):
    """API on a private copy of the demo store (SQLite file or PostgreSQL database)."""
    from conftest import clone_store
    from mqi import config

    store = clone_store(demo["db"], tmp_path)
    if str(store).startswith("postgres"):
        monkeypatch.setenv("MQI_DATABASE_URL", str(store))
    else:
        monkeypatch.delenv("MQI_DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_PATH", store)
    from api import main

    main._cache.clear()
    return TestClient(main.app)


def _blocked_oos_lot(c):
    lots = c.get("/api/v1/lots", params={"status": "block"}).json()
    return next(x["lot_id"] for x in lots if any("OOS" in r["en"] for r in x["reasons"]))


def test_health_and_docs(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["demo_version"]
    assert client.get("/openapi.json").status_code == 200


def test_lot_list_and_release_detail(client):
    lots = client.get("/api/v1/lots", params={"limit": 40}).json()
    assert len(lots) == 40 and {x["status"] for x in lots} <= {"release", "hold", "block"}
    lot = _blocked_oos_lot(client)
    d = client.get(f"/api/v1/lots/{lot}/release").json()
    assert d["status"] == "block" and "viscosity" in d["values"]
    assert client.get("/api/v1/lots/NOPE/release").status_code == 404


def test_oos_flow_over_http(client):
    lot = _blocked_oos_lot(client)
    hist = client.get(f"/api/v1/lots/{lot}/history", params={"property": "viscosity"}).json()
    assert [r["oos"] for r in hist] == [True, False]

    # a further retest is refused until the failing result is under investigation
    r = client.post(f"/api/v1/lots/{lot}/retests",
                    json={"property": "viscosity", "value": 3490, "attempt": "remeasure",
                          "reason": "check", "operator": "Kim"})
    assert r.status_code == 409

    inv = client.post("/api/v1/oos", json={"lot_id": lot, "property": "viscosity", "opened_by": "QC Lee"})
    assert inv.status_code == 201
    no = inv.json()["no"]
    assert client.get(f"/api/v1/lots/{lot}/release").json()["status"] == "block"

    bad = client.post(f"/api/v1/oos/{no}/phase1", json={"lab_error": True, "cause": "x", "by": "Lee"})
    assert bad.status_code == 409                       # a lab error needs its category

    ok = client.post(f"/api/v1/oos/{no}/phase1",
                     json={"lab_error": True, "category": "instrument", "by": "Lee",
                           "cause": "Torque zero check failed; passed after re-zero"})
    assert ok.json()["outcome"] == "invalidated"
    d = client.get(f"/api/v1/lots/{lot}/release").json()
    assert d["status"] == "release"
    hist = client.get(f"/api/v1/lots/{lot}/history", params={"property": "viscosity"}).json()
    assert [r["invalidated"] for r in hist] == [True, False]          # first result kept on record


def test_validation_errors_are_422(client):
    r = client.post("/api/v1/oos/OOS-X/phase2", json={"finding": "x", "disposition": "melt", "by": "a"})
    assert r.status_code == 422


def test_findings(client):
    f = client.get("/api/v1/findings").json()
    assert any(x["key"].startswith("oos_") for x in f)
