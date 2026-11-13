"""SQLite store. One file, no server. Every analysis reads from here."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    lot_id TEXT PRIMARY KEY, material TEXT, supplier TEXT, made_at TEXT
);
CREATE TABLE IF NOT EXISTS genealogy (
    product_lot TEXT, material TEXT, raw_lot TEXT,
    PRIMARY KEY (product_lot, material)
);
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id TEXT, material TEXT, property TEXT, value REAL, unit TEXT,
    source TEXT,               -- inhouse | coa
    instrument TEXT, profile TEXT, operator TEXT,
    measured_at TEXT, source_file TEXT,
    attempt TEXT,              -- initial | remeasure | resample (NULL = as ingested)
    reason TEXT,               -- why a repeat test was run
    UNIQUE (lot_id, property, source, source_file)
);
-- OOS investigation. Measurements are never edited: a result is invalidated only by a closed
-- investigation that proved a laboratory error, so the original value always stays on record.
CREATE TABLE IF NOT EXISTS oos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, no TEXT UNIQUE,
    lot_id TEXT, property TEXT, value REAL, source_file TEXT, measured_at TEXT,
    opened TEXT, opened_by TEXT,
    phase1 TEXT,               -- pending | lab_error | no_lab_error
    phase1_category TEXT, phase1_cause TEXT, phase1_by TEXT, phase1_at TEXT,
    phase2_finding TEXT, disposition TEXT, phase2_by TEXT, phase2_at TEXT,
    outcome TEXT               -- open | invalidated | confirmed
);
CREATE TABLE IF NOT EXISTS msa (
    study TEXT, property TEXT, instrument TEXT,
    part TEXT, operator TEXT, replicate INTEGER, value REAL
);
CREATE TABLE IF NOT EXISTS process (
    lot_id TEXT, param TEXT, value REAL, text TEXT, PRIMARY KEY (lot_id, param)
);
CREATE TABLE IF NOT EXISTS changes (
    change_no TEXT PRIMARY KEY, date TEXT, category TEXT, item TEXT, description TEXT,
    owner TEXT, notified TEXT
);
CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT, no TEXT, received TEXT, customer TEXT, lot_id TEXT,
    property TEXT, symptom TEXT, qty REAL, unit TEXT, severity TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT, step TEXT, kind TEXT, action TEXT,
    verification TEXT, owner TEXT, due TEXT, status TEXT, created TEXT, closed TEXT, note TEXT,
    UNIQUE (ref, step, action)
);
CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT, file TEXT, kind TEXT, profile TEXT, confidence REAL,
    rows INTEGER, status TEXT, note TEXT
);
"""


def _migrate(con) -> None:
    """Add columns introduced after a database was first created."""
    have = {r[1] for r in con.execute("PRAGMA table_info(measurements)")}
    for col in ("attempt", "reason"):
        if col not in have:
            con.execute(f"ALTER TABLE measurements ADD COLUMN {col} TEXT")


@contextmanager
def connect(path: Path | None = None):
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        yield con
        con.commit()
    finally:
        con.close()


def read(sql: str, params: tuple = (), path: Path | None = None) -> pd.DataFrame:
    with connect(path) as con:
        return pd.read_sql_query(sql, con, params=params)


def upsert_measurements(df: pd.DataFrame, path: Path | None = None) -> int:
    cols = ["lot_id", "material", "property", "value", "unit", "source",
            "instrument", "profile", "operator", "measured_at", "source_file"]
    df = df.reindex(columns=cols)
    with connect(path) as con:
        before = con.total_changes
        con.executemany(
            f"INSERT OR REPLACE INTO measurements ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            df.astype(object).where(df.notna(), None).values.tolist(),
        )
        return con.total_changes - before


def write_table(name: str, df: pd.DataFrame, path: Path | None = None, replace: bool = False) -> None:
    with connect(path) as con:
        if replace:
            con.execute(f"DELETE FROM {name}")
        df.to_sql(name, con, if_exists="append", index=False)


def log_ingest(file: str, kind: str, profile: str | None, confidence: float | None,
               rows: int, status: str, note: str = "", path: Path | None = None) -> None:
    with connect(path) as con:
        con.execute(
            "INSERT INTO ingest_log (at, file, kind, profile, confidence, rows, status, note) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), file, kind, profile, confidence, rows, status, note),
        )


def reset(path: Path | None = None) -> None:
    p = Path(path or config.DB_PATH)
    if p.exists():
        p.unlink()


# ---- convenience views -------------------------------------------------------

RANK = {"initial": 0, "remeasure": 1, "resample": 1}


def product_history(lot: str | None = None, prop: str | None = None, path: Path | None = None) -> pd.DataFrame:
    """Every in-house product result in test order, with seq (1 = first test) and invalidated flag."""
    q, p = ("SELECT id, lot_id, property, value, unit, instrument, operator, measured_at, source_file, attempt, "
            "reason FROM measurements WHERE material='product' AND source='inhouse'"), []
    if lot:
        q, p = q + " AND lot_id=?", p + [lot]
    if prop:
        q, p = q + " AND property=?", p + [prop]
    m = read(q, tuple(p), path)
    if m.empty:
        return m.assign(seq=pd.Series(dtype=int), invalidated=pd.Series(dtype=bool))
    m["_r"] = m["attempt"].map(RANK).fillna(0)
    m = m.sort_values(["measured_at", "_r", "id"]).drop(columns="_r")
    m["seq"] = m.groupby(["lot_id", "property"]).cumcount() + 1
    inv = read("SELECT lot_id, property, source_file FROM oos WHERE outcome='invalidated'", (), path)
    key = set(map(tuple, inv.values.tolist()))
    m["invalidated"] = [k in key for k in zip(m["lot_id"], m["property"], m["source_file"])]
    return m.reset_index(drop=True)


def product_results(prop: str, path: Path | None = None) -> pd.DataFrame:
    """The reportable result per lot: the latest result that was not invalidated by an OOS investigation."""
    h = product_history(prop=prop, path=path)
    return h[~h["invalidated"]].groupby("lot_id").tail(1).reset_index(drop=True) if len(h) else h


def product_wide(source: str = "inhouse", path: Path | None = None) -> pd.DataFrame:
    """One row per product lot, one column per property (reportable value).

    In-house: latest result in test order, excluding results invalidated by an OOS investigation.
    Whether the lot can ship despite an earlier failing result is decided in outgoing.release_check.
    """
    if source == "inhouse":
        m = product_history(path=path)
        m = m[~m["invalidated"]] if len(m) else m
    else:
        m = read("SELECT lot_id, property, value, measured_at FROM measurements "
                 "WHERE material='product' AND source=?", (source,), path).sort_values("measured_at")
    if m.empty:
        return m
    w = m.pivot_table(index="lot_id", columns="property", values="value", aggfunc="last", sort=False)
    t = m.groupby("lot_id")["measured_at"].min().rename("measured_at")
    return w.join(t).sort_values("measured_at").reset_index()


def raw_wide(path: Path | None = None) -> pd.DataFrame:
    """Raw-material lots with CoA and in-house values side by side."""
    m = read("SELECT m.lot_id, m.material, l.supplier, m.property, m.source, m.value, l.made_at "
             "FROM measurements m LEFT JOIN lots l ON m.lot_id=l.lot_id WHERE m.material!='product'",
             (), path)
    return m
