"""Customer complaints and the corrective-action register (8D / SCAR actions with owner, due date, status)."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from . import db

STATUS = ["Open", "In progress", "Done", "Verified"]
STATUS_KO = {"Open": "미착수", "In progress": "진행", "Done": "완료", "Verified": "유효성 확인"}


# ---------------------------------------------------------------- complaints

def add_complaint(customer: str, lot_id: str, prop: str, symptom: str, qty: float = 0, unit: str = "kg",
                  severity: str = "major", received: str | None = None, dbpath=None) -> str:
    received = received or date.today().isoformat()
    with db.connect(dbpath) as con:
        n = con.execute("SELECT COUNT(*) FROM complaints").fetchone()[0] + 1
        no = f"CC-{received[2:4]}{received[5:7]}-{n:03d}"
        con.execute("INSERT INTO complaints (no, received, customer, lot_id, property, symptom, qty, unit, severity, status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (no, received, customer, lot_id, prop, symptom, qty, unit, severity, "Open"))
    return no


def complaints(dbpath=None) -> pd.DataFrame:
    return db.read("SELECT * FROM complaints ORDER BY received DESC, id DESC", (), dbpath)


def complaint(no: str, dbpath=None) -> dict | None:
    c = db.read("SELECT * FROM complaints WHERE no=?", (no,), dbpath)
    return c.iloc[0].to_dict() if len(c) else None


# ---------------------------------------------------------------- actions

def register(ref: str, rows: list[dict], dbpath=None) -> int:
    """Add actions once (same ref + step + action is ignored). Returns how many were new."""
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect(dbpath) as con:
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO actions (ref, step, kind, action, verification, owner, due, status, created)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(ref, r["step"], r.get("kind", ""), r["action"], r.get("verification", ""), r.get("owner", ""),
              r.get("due", ""), r.get("status", "Open"), now) for r in rows])
        return con.total_changes - before


def actions(ref: str | None = None, dbpath=None) -> pd.DataFrame:
    if ref:
        a = db.read("SELECT * FROM actions WHERE ref=? ORDER BY id", (ref,), dbpath)
    else:
        a = db.read("SELECT * FROM actions ORDER BY due, id", (), dbpath)
    if len(a):
        today = date.today().isoformat()
        a["overdue"] = (a["status"].isin(["Open", "In progress"])) & (a["due"] < today)
    return a


def update(rows: pd.DataFrame, dbpath=None) -> None:
    """Write back edited owner / due / status / note. Closing sets the closed date."""
    now = date.today().isoformat()
    with db.connect(dbpath) as con:
        for _, r in rows.iterrows():
            closed = now if r["status"] in ("Done", "Verified") else None
            con.execute("UPDATE actions SET owner=?, due=?, status=?, note=?, "
                        "closed=COALESCE(CASE WHEN ? IS NULL THEN NULL ELSE closed END, ?) WHERE id=?",
                        (r["owner"], str(r["due"])[:10], r["status"], r.get("note"), closed, closed, int(r["id"])))


def summary(dbpath=None) -> dict:
    a = actions(dbpath=dbpath)
    if a.empty:
        return {"total": 0, "open": 0, "overdue": 0, "done": 0}
    return {"total": len(a), "open": int(a["status"].isin(["Open", "In progress"]).sum()),
            "overdue": int(a["overdue"].sum()), "done": int(a["status"].isin(["Done", "Verified"]).sum())}
