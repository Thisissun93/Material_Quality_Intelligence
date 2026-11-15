"""Users, roles, two-signature release approval bound to an evidence hash, and the audit trail."""
import sqlite3

import pytest

from conftest import login

pytest.importorskip("fastapi")

PW = {"password": "demo1234"}


def _release_lot(c, stage="none"):
    for x in c.get("/api/v1/lots", params={"status": "release"}).json():
        if c.get(f"/api/v1/lots/{x['lot_id']}/approval").json()["stage"] == stage:
            return x["lot_id"]
    raise AssertionError(stage)


def test_sign_in_required_and_roles(client):
    anon = client.__class__(client.app)
    assert anon.get("/health").status_code == 200
    assert anon.get("/api/v1/lots").status_code == 401
    assert anon.post("/api/v1/auth/token", json={"username": "qa.lee", "password": "nope"}).status_code == 401
    assert client.get("/api/v1/auth/me").json()["role"] == "qa"

    viewer = login(client, "viewer")
    assert client.get("/api/v1/lots", headers=viewer).status_code == 200
    r = client.post("/api/v1/oos", json={"lot_id": "X", "property": "viscosity"}, headers=viewer)
    assert r.status_code == 403
    lot = _release_lot(client)
    assert client.post(f"/api/v1/lots/{lot}/approve", json=PW, headers=login(client, "qc.kim")).status_code == 403
    assert client.get("/api/v1/audit", headers=login(client, "qc.kim")).status_code == 403


def test_review_then_approve_by_another_person(client):
    lot = _release_lot(client)
    ap = login(client, "approver.park")
    assert client.post(f"/api/v1/lots/{lot}/approve", json=PW, headers=ap).status_code == 409   # no review yet
    bad = client.post(f"/api/v1/lots/{lot}/review", json={"password": "wrong"})
    assert bad.status_code == 403                                                              # e-signature
    r = client.post(f"/api/v1/lots/{lot}/review", json=PW).json()
    assert r["stage"] == "reviewed" and not r["coa_ready"]
    assert client.post(f"/api/v1/lots/{lot}/review", json=PW).status_code == 409                # nothing changed
    r = client.post(f"/api/v1/lots/{lot}/approve", json=PW, headers=ap).json()
    assert r["stage"] == "approved" and r["coa_ready"]
    rev, app_ = r["history"][-2:]
    assert (rev["actor"], app_["actor"]) == ("qa.lee", "approver.park")
    assert rev["evidence_hash"] == app_["evidence_hash"] == r["current_hash"]

    ev = client.get(f"/api/v1/release-decisions/{app_['id']}/evidence").json()
    assert ev["recomputed_hash"] == ev["evidence_hash"]
    assert {"values", "tests", "oos", "equipment", "open_complaints", "spec"} <= set(ev["evidence"]["data"])


def test_evidence_change_invalidates_signatures(client):
    lot = _release_lot(client)
    ap = login(client, "approver.park")
    client.post(f"/api/v1/lots/{lot}/review", json=PW)
    # a retest after the QA review: the approver is refused, QA must review again
    hist = client.get(f"/api/v1/lots/{lot}/history", params={"property": "tg"}).json()
    client.post(f"/api/v1/lots/{lot}/retests", json={"property": "tg", "value": hist[-1]["value"],
                                                    "attempt": "remeasure", "reason": "check"},
                headers=login(client, "qc.kim"))
    st = client.get(f"/api/v1/lots/{lot}/approval").json()
    assert st["stage"] == "reviewed" and st["stale"]
    r = client.post(f"/api/v1/lots/{lot}/approve", json=PW, headers=ap)
    assert r.status_code == 409 and "review" in r.json()["detail"]

    # review again (another QA), approve, then new evidence after approval: the approval no longer covers the lot
    assert client.post(f"/api/v1/lots/{lot}/review", json=PW, headers=login(client, "qa.choi")).status_code == 200
    assert client.post(f"/api/v1/lots/{lot}/approve", json=PW, headers=ap).json()["coa_ready"]
    client.post(f"/api/v1/lots/{lot}/retests", json={"property": "tg", "value": hist[-1]["value"],
                                                    "attempt": "resample", "reason": "customer request"},
                headers=login(client, "qc.kim"))
    st = client.get(f"/api/v1/lots/{lot}/approval").json()
    assert st["stage"] == "approved" and st["stale"] and not st["coa_ready"]


def test_reviewer_cannot_approve_and_blocked_lots_cannot_be_signed(client, demo):
    from mqi import approval, auth, engine

    b = engine.run()
    lot = _release_lot(client)
    client.post(f"/api/v1/lots/{lot}/review", json=PW)
    # an approver who is also the reviewer: only possible if roles change; the rule is still enforced
    qa = auth.get_user("qa.lee")
    with pytest.raises(approval.Denied):
        approval.approve(b, lot, qa, "demo1234")
    blocked = client.get("/api/v1/lots", params={"status": "block"}).json()[0]["lot_id"]
    r = client.post(f"/api/v1/lots/{blocked}/review", json=PW)
    assert r.status_code == 409
    hold = client.get("/api/v1/lots", params={"status": "hold"}).json()[0]["lot_id"]
    assert client.post(f"/api/v1/lots/{hold}/review", json=PW).status_code == 409            # needs a comment
    assert client.post(f"/api/v1/lots/{hold}/review", json={**PW, "comment": "border value within MU"}).status_code == 200
    rej = client.post(f"/api/v1/lots/{hold}/reject", json=PW, headers=login(client, "approver.park"))
    assert rej.status_code == 409                                                             # needs a comment
    rej = client.post(f"/api/v1/lots/{hold}/reject", json={**PW, "comment": "retest first"},
                      headers=login(client, "approver.park"))
    assert rej.json()["stage"] == "rejected"


def test_audit_trail_is_append_only_and_chained(client):
    lot = _release_lot(client)
    client.post(f"/api/v1/lots/{lot}/review", json=PW)
    a = client.get("/api/v1/audit", params={"target": f"lot/{lot}"}).json()
    assert a[0]["action"] == "release.review" and a[0]["actor"] == "qa.lee"
    assert client.get("/api/v1/audit/verify").json()["ok"]

    from mqi import audit, db

    if db.is_pg(db.target()):
        import psycopg
        with pytest.raises(psycopg.Error):
            with psycopg.connect(db.target()) as con:
                con.execute("UPDATE audit_log SET actor='x' WHERE id=3")
        return
    path = db.target()
    con = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE audit_log SET actor='x' WHERE id=3")
    con.rollback()
    # someone with file access drops the guard and edits a row: the chain shows where
    con.execute("DROP TRIGGER audit_log_no_update")
    con.execute("UPDATE audit_log SET actor='x' WHERE id=3")
    con.commit()
    con.close()
    v = audit.verify(path)
    assert not v["ok"] and v["broken_at"] == 3


def test_demo_has_an_approved_lot_and_a_signed_coa(bundle):
    from mqi import approval, outgoing

    st = approval.table(bundle, 12)
    done = [lot for lot, s in st.items() if s["coa_ready"]]
    assert done and any(s["stage"] == "reviewed" for s in st.values())
    pdf = outgoing.pdf(bundle, done[0], "Customer A", signoff=approval.signoff(st[done[0]]))
    assert pdf[:4] == b"%PDF"
