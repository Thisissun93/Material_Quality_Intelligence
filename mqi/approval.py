"""Release review and approval with the evidence each signature was given.

Two people sign a release:
  1. QA reviews the lot (role qa) and signs the review.
  2. The approver (role approver) approves or rejects it. The approver cannot be the reviewer.

Each signature re-enters the signer's password (electronic signature) and stores a snapshot of the lot's
evidence: reportable values, the full test history including invalidated results, OOS investigations,
equipment runs and FDC alarms, open complaints and the specification. The SHA-256 of that snapshot is the
evidence hash. If anything in it changes after QA signed (a retest, a new equipment run, a complaint), the
approver is refused and QA must review again; if it changes after approval, the approval no longer covers
the lot and the formal CoA cannot be issued until the lot is reviewed and approved again.

The hash covers the lot's own records, not the plant-wide judgement (e.g. the T² model is refitted as new lots
arrive); the release level and reasons at signing time are stored next to it.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from . import audit, auth, config, db, fdc, oos, outgoing, qms


class Denied(Exception):
    """The signed-in user may not do this (role, wrong signature password)."""


MEANING = {
    "review": ("QA 검토: 이 로트의 출하 판정 근거를 확인했습니다",
               "QA review: I have checked the evidence for releasing this lot"),
    "approve": ("출하 승인: 위 근거로 이 로트의 출하를 승인합니다",
                "Release approval: I approve this lot for release on the evidence above"),
    "reject": ("출하 반려: 이 로트의 출하를 승인하지 않습니다",
               "Release rejected: I do not approve this lot for release"),
}
STAGE_KO = {"none": "미검토", "reviewed": "QA 검토 완료 · 승인 대기", "approved": "출하 승인", "rejected": "반려"}
STAGE_EN = {"none": "Not reviewed", "reviewed": "QA reviewed · awaiting approval", "approved": "Approved",
            "rejected": "Rejected"}


def _f(v):
    return None if v is None or (isinstance(v, float) and v != v) else (float(v) if isinstance(v, (int, float)) else v)


def evidence(b, lot: str) -> dict:
    """What a signature covers. 'data' is hashed; 'decision' records the judgement at that moment."""
    level, why = outgoing.release_check(b, lot)
    spec = config.specs()["product"]["properties"]
    row = b.product[b.product["lot_id"] == lot]
    values = {p: _f(row.iloc[0].get(p)) for p in spec} if len(row) else {}
    h = oos.history(lot, dbpath=b.dbpath)
    tests = [] if h.empty else [
        {"id": int(r["id"]), "property": r["property"], "seq": int(r["seq"]), "attempt": r["attempt"],
         "value": _f(r["value"]), "invalidated": bool(r["invalidated"]), "oos_no": r["oos_no"] or None,
         "operator": r["operator"], "measured_at": r["measured_at"]}
        for r in h.astype(object).where(h.notna(), None).to_dict("records")]
    inv = oos.investigations(b.dbpath)
    inv = inv[inv["lot_id"] == lot] if len(inv) else inv
    invs = [{"no": r["no"], "property": r["property"], "phase1": r["phase1"], "phase1_category": r["phase1_category"],
             "disposition": r["disposition"], "outcome": r["outcome"]}
            for r in inv.astype(object).where(inv.notna(), None).to_dict("records")]
    runs = fdc.runs(lot, dbpath=b.dbpath)
    eq = []
    for r in runs.sort_values("id").to_dict("records"):
        al = fdc.run_detail(int(r["id"]), b.dbpath)["alarms"] if r["status"] == "alarm" else pd.DataFrame()
        eq.append({"run": int(r["id"]), "equipment": r["equipment"], "started": r["started"], "status": r["status"],
                   "alarms": sorted(f"{a['step']}:{a['rule']}:{a['tag']}" for a in al.to_dict("records"))})
    cc = qms.complaints(b.dbpath)
    comp = sorted(cc.loc[(cc["lot_id"] == lot) & (cc["status"] == "Open"), "no"].tolist()) if len(cc) else []
    data = {"lot_id": lot, "values": values, "tests": tests, "oos": invs, "equipment": eq, "open_complaints": comp,
            "spec": {p: {k: s.get(k) for k in ("lsl", "usl", "unit")} for p, s in spec.items()}}
    return {"data": data, "decision": {"level": level, "reasons": [{"ko": k, "en": e} for k, e in why]}}


def digest(ev: dict) -> str:
    return hashlib.sha256(audit.canonical(ev["data"]).encode()).hexdigest()


def decisions(lot: str, dbpath=None, with_evidence: bool = False) -> pd.DataFrame:
    cols = "*" if with_evidence else ("id, lot_id, action, level, comment, evidence_hash, actor, actor_name, role, "
                                      "at, meaning")
    return db.read(f"SELECT {cols} FROM release_decisions WHERE lot_id=? ORDER BY id", (lot,), dbpath)


def decision(did: int, dbpath=None) -> dict | None:
    r = db.read("SELECT * FROM release_decisions WHERE id=?", (int(did),), dbpath)
    if r.empty:
        return None
    d = r.iloc[0].to_dict()
    d["evidence"] = json.loads(d["evidence"])
    return d


def state(b, lot: str, ev: dict | None = None) -> dict:
    """{stage, stale, current_hash, level, last, review, approval, coa_ready}."""
    ev = ev or evidence(b, lot)
    cur = digest(ev)
    d = decisions(lot, b.dbpath)
    recs = d.astype(object).where(d.notna(), None).to_dict("records")
    last = recs[-1] if recs else None
    stage = {"review": "reviewed", "approve": "approved", "reject": "rejected"}[last["action"]] if last else "none"
    stale = stage in ("reviewed", "approved") and last["evidence_hash"] != cur
    review = next((r for r in reversed(recs) if r["action"] == "review"), None)
    appr = last if stage == "approved" else None
    return dict(stage=stage, stale=stale, current_hash=cur, level=ev["decision"]["level"], last=last,
                review=review if stage in ("reviewed", "approved") else None, approval=appr,
                coa_ready=stage == "approved" and not stale, history=recs)


def _sign(b, lot: str, user: dict, password: str, action: str, comment: str, perm: str) -> dict:
    if not auth.can(user, perm):
        raise Denied(f"{auth.ROLE_KO.get(user.get('role') if user else '', '로그인 필요')} 권한으로는 할 수 없습니다 / "
                     f"role {user.get('role') if user else None} may not {action}")
    if not auth.check_password(user["username"], password, purpose="esign", dbpath=b.dbpath):
        raise Denied("서명 비밀번호가 맞지 않습니다 / signature password is wrong")
    comment = (comment or "").strip()
    ev = evidence(b, lot)
    st = state(b, lot, ev)
    level = ev["decision"]["level"]
    if action == "review":
        if level == "block":
            raise ValueError("출하 불가 판정 로트는 검토·승인할 수 없습니다 / a blocked lot cannot be reviewed")
        if st["stage"] in ("reviewed", "approved") and not st["stale"]:
            raise ValueError("이미 검토된 로트입니다 (근거 변경 없음) / already reviewed and nothing has changed")
    else:
        if st["stage"] != "reviewed":
            raise ValueError("QA 검토가 끝난 로트만 승인·반려할 수 있습니다 / the lot needs a QA review first")
        if st["review"]["actor"] == user["username"]:
            raise ValueError("검토자와 승인자는 다른 사람이어야 합니다 / the reviewer cannot approve")
        if action == "approve" and st["stale"]:
            raise ValueError("QA 검토 이후 근거 데이터가 바뀌었습니다. QA 재검토가 필요합니다 / "
                             "the evidence changed after the QA review: review again")
        if action == "approve" and level == "block":
            raise ValueError("출하 불가 판정 로트는 승인할 수 없습니다 / a blocked lot cannot be approved")
    if (level == "hold" and action != "reject" or action == "reject") and not comment:
        raise ValueError("보류 로트의 검토·승인과 반려에는 판단 근거(코멘트)가 필요합니다 / "
                         "a comment is required for a hold lot and for a rejection")
    h = digest(ev)
    with db.connect(b.dbpath) as con:
        cur = con.execute(
            "INSERT INTO release_decisions (lot_id, action, level, comment, evidence, evidence_hash, actor, actor_name, "
            "role, at, meaning) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (lot, action, level, comment, audit.canonical(ev), h, user["username"], user["name"], user["role"],
             audit._now(), MEANING[action][1]))
        did = cur.lastrowid
        audit.record(con, user["username"], f"release.{action}", f"lot/{lot}",
                     {"decision_id": did, "level": level, "evidence_hash": h, "comment": comment}, user["role"])
    return state(b, lot)


def review(b, lot: str, user: dict, password: str, comment: str = "") -> dict:
    return _sign(b, lot, user, password, "review", comment, "release.review")


def approve(b, lot: str, user: dict, password: str, comment: str = "") -> dict:
    return _sign(b, lot, user, password, "approve", comment, "release.approve")


def reject(b, lot: str, user: dict, password: str, comment: str) -> dict:
    return _sign(b, lot, user, password, "reject", comment, "release.approve")


def signoff(st: dict) -> dict | None:
    """What the CoA prints in its signature block (only for an approval that still covers the lot)."""
    if not st["coa_ready"]:
        return None
    r, a = st["review"], st["approval"]
    # user IDs, not display names: the PDF uses the standard fonts, and the ID is what the audit trail shows
    return {"reviewed_by": r["actor"], "reviewed_at": r["at"], "approved_by": a["actor"],
            "approved_at": a["at"], "evidence_hash": a["evidence_hash"]}


def table(b, n: int = 40) -> dict[str, dict]:
    """Approval state of the latest n lots (for the shipping list). Only signed lots need their evidence."""
    lots = b.product["lot_id"].tail(n).tolist()
    signed = set(db.read("SELECT DISTINCT lot_id FROM release_decisions", (), b.dbpath)["lot_id"])
    none = dict(stage="none", stale=False, coa_ready=False, last=None, review=None, approval=None, history=[])
    return {lot: state(b, lot) if lot in signed else dict(none) for lot in lots}
