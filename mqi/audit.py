"""Audit trail: who did what, when, to which record.

Rows are only ever added (the database refuses UPDATE and DELETE on this table, see db.APPEND_ONLY).
Every row also stores the hash of the row before it, so a row changed or removed behind the database's
back (e.g. by someone with direct file access who dropped the trigger) breaks the chain and verify() says
where.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import pandas as pd

from . import db

GENESIS = "0" * 64
_FIELDS = ("at", "actor", "role", "action", "target", "detail", "prev_hash")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(row: dict) -> str:
    return hashlib.sha256(canonical({k: row[k] for k in _FIELDS}).encode()).hexdigest()


def record(con, actor: str, action: str, target: str = "", detail: dict | None = None,
           role: str | None = None) -> str:
    """Append one entry inside the caller's transaction (so the entry and the change commit together)."""
    if isinstance(con, db._PgConnection):
        con.execute("SELECT pg_advisory_xact_lock(7701)")     # one writer at a time extends the chain
    last = con.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    row = dict(at=_now(), actor=actor or "", role=role or "", action=action, target=target or "",
               detail=canonical(detail or {}), prev_hash=last[0] if last else GENESIS)
    row["hash"] = _hash(row)
    cols = list(row)
    con.execute(f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(row.values()))
    return row["hash"]


def log(actor: str, action: str, target: str = "", detail: dict | None = None, role: str | None = None,
        dbpath=None) -> str:
    with db.connect(dbpath) as con:
        return record(con, actor, action, target, detail, role)


def entries(target: str | None = None, action: str | None = None, limit: int = 500, dbpath=None) -> pd.DataFrame:
    q, p = "SELECT * FROM audit_log WHERE 1=1", []
    if target:
        q, p = q + " AND (target=? OR target LIKE ?)", p + [target, f"{target}/%"]
    if action:
        q, p = q + " AND action LIKE ?", p + [f"{action}%"]
    return db.read(q + f" ORDER BY id DESC LIMIT {int(limit)}", tuple(p), dbpath)


def verify(dbpath=None) -> dict:
    """Walk the whole chain. {ok, rows, broken_at (id or None), reason}."""
    df = db.read("SELECT * FROM audit_log ORDER BY id", (), dbpath)
    prev = GENESIS
    for r in df.to_dict("records"):
        if r["prev_hash"] != prev:
            return dict(ok=False, rows=len(df), broken_at=int(r["id"]), reason="previous row missing or changed")
        if _hash(r) != r["hash"]:
            return dict(ok=False, rows=len(df), broken_at=int(r["id"]), reason="row content changed")
        prev = r["hash"]
    return dict(ok=True, rows=len(df), broken_at=None, reason="")
