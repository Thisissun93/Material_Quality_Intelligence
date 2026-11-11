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
    UNIQUE (lot_id, property, source, source_file)
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


@contextmanager
def connect(path: Path | None = None):
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
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

def product_wide(source: str = "inhouse", path: Path | None = None) -> pd.DataFrame:
    """One row per product lot, one column per property (latest value)."""
    m = read("SELECT lot_id, property, value, measured_at FROM measurements "
             "WHERE material='product' AND source=?", (source,), path)
    if m.empty:
        return m
    w = m.sort_values("measured_at").pivot_table(index="lot_id", columns="property",
                                                 values="value", aggfunc="last")
    t = m.groupby("lot_id")["measured_at"].min().rename("measured_at")
    return w.join(t).sort_values("measured_at").reset_index()


def raw_wide(path: Path | None = None) -> pd.DataFrame:
    """Raw-material lots with CoA and in-house values side by side."""
    m = read("SELECT m.lot_id, m.material, l.supplier, m.property, m.source, m.value, l.made_at "
             "FROM measurements m LEFT JOIN lots l ON m.lot_id=l.lot_id WHERE m.material!='product'",
             (), path)
    return m
