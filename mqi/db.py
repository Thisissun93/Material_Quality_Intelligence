"""Storage. SQLite file by default (demo, Streamlit Cloud, tests); PostgreSQL when MQI_DATABASE_URL is set.

Every module talks to the database through connect() and read(), using SQLite-style SQL ("?" placeholders,
INSERT OR REPLACE / OR IGNORE). For PostgreSQL the statements are translated in one place (_pg_sql), so the
analysis code stays the same on both backends.
"""
from __future__ import annotations

import re
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
-- key/value: demo_version (which demo build created the data), rev (bumped on every write)
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
"""

# columns per table (for INSERT without a column list) and the conflict key used by INSERT OR REPLACE
def _columns(body: str) -> list[str]:
    body, parts, depth, cur = re.sub(r"--[^\n]*", "", body), [], 0, ""
    for ch in body:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [x.split()[0] for x in (q.strip() for q in parts)
            if x and not x.upper().startswith(("PRIMARY KEY", "UNIQUE"))]


_COLS = {t: _columns(body) for t, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S)}
_KEYS = {"lots": ["lot_id"], "genealogy": ["product_lot", "material"], "process": ["lot_id", "param"],
         "changes": ["change_no"], "measurements": ["lot_id", "property", "source", "source_file"],
         "meta": ["key"]}
_ready: set[str] = set()


# ---------------------------------------------------------------- target

def target(path=None):
    """What to open: an explicit path/URL, else MQI_DATABASE_URL, else the default SQLite file."""
    if path is None:
        return config.database_url() or config.DB_PATH
    return path


def is_pg(t) -> bool:
    return isinstance(t, str) and t.startswith(("postgresql://", "postgres://"))


def backend(path=None) -> str:
    return "postgresql" if is_pg(target(path)) else "sqlite"


# ---------------------------------------------------------------- PostgreSQL translation

def _pg_sql(sql: str) -> str:
    s = sql.replace("%", "%%").replace("?", "%s")
    m = re.match(r"\s*INSERT OR (REPLACE|IGNORE) INTO (\w+)\s*(\(([^)]*)\))?\s*VALUES", s, re.I)
    if not m:
        return s
    kind, table, cols = m.group(1).upper(), m.group(2), m.group(4)
    cols = [c.strip() for c in cols.split(",")] if cols else _COLS[table]
    body = s[m.end():]
    head = f"INSERT INTO {table} ({', '.join(cols)}) VALUES"
    if kind == "IGNORE":
        return f"{head}{body} ON CONFLICT DO NOTHING"
    key = _KEYS[table]
    upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in key)
    return f"{head}{body} ON CONFLICT ({', '.join(key)}) DO " + (f"UPDATE SET {upd}" if upd else "NOTHING")


def _pg_schema() -> list[str]:
    s = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")
    return [x for x in (re.sub(r"--[^\n]*", "", q).strip() for q in s.split(";")) if x]


class _Cursor:
    """sqlite3-like cursor over psycopg (fetchone/fetchall/lastrowid)."""

    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description


class _PgConnection:
    """The subset of sqlite3.Connection that MQI uses, on top of psycopg."""

    def __init__(self, url: str):
        import psycopg

        self._con = psycopg.connect(url, cursor_factory=psycopg.ClientCursor)
        self.total_changes = 0
        self.wrote = False

    def execute(self, sql: str, params=()):
        q = _pg_sql(sql)
        is_insert = q.lstrip().upper().startswith("INSERT")
        if is_insert and " RETURNING " not in q.upper() and "ON CONFLICT" not in q.upper() and _has_id(q):
            q += " RETURNING id"
            cur = self._con.cursor()
            cur.execute(q, tuple(params))
            row = cur.fetchone()
            self._count(cur)
            return _Cursor(cur, row[0] if row else None)
        cur = self._con.cursor()
        cur.execute(q, tuple(params))
        self._count(cur)
        return _Cursor(cur)

    def executemany(self, sql: str, seq):
        cur = self._con.cursor()
        rows = [tuple(r) for r in seq]
        if rows:
            cur.executemany(_pg_sql(sql), rows)
            self._count(cur)
        return _Cursor(cur)

    def _count(self, cur):
        if cur.description is None:          # not a SELECT
            self.wrote = True
            self.total_changes += max(cur.rowcount, 0)

    def commit(self):
        self._con.commit()

    def rollback(self):
        self._con.rollback()

    def close(self):
        self._con.close()


def _has_id(sql: str) -> bool:
    m = re.match(r"\s*INSERT INTO (\w+)", sql, re.I)
    return bool(m) and "id" in _COLS.get(m.group(1), [])


# ---------------------------------------------------------------- connect / read

def _migrate(con, pg: bool) -> None:
    """Add columns introduced after a database was first created."""
    if pg:
        have = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns "
                                          "WHERE table_name='measurements'").fetchall()}
    else:
        have = {r[1] for r in con.execute("PRAGMA table_info(measurements)")}
    for col in ("attempt", "reason"):
        if col not in have:
            con.execute(f"ALTER TABLE measurements ADD COLUMN {col} TEXT")


@contextmanager
def connect(path=None):
    """A connection to the store. Commits on success, rolls back on error."""
    t = target(path)
    if is_pg(t):
        con = _PgConnection(t)
        try:
            if t not in _ready:
                for q in _pg_schema():
                    con._con.execute(q)
                _migrate(con, True)
                con.commit()
                _ready.add(t)
            con.wrote = False
            yield con
            if con.wrote:
                con._con.execute("INSERT INTO meta (key, value) VALUES ('rev', '1') ON CONFLICT (key) DO UPDATE "
                                 "SET value = (CAST(meta.value AS INTEGER) + 1)::TEXT")
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return
    path = Path(t)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        _migrate(con, False)
        yield con
        con.commit()
    finally:
        con.close()


def read(sql: str, params: tuple = (), path=None) -> pd.DataFrame:
    with connect(path) as con:
        if isinstance(con, _PgConnection):
            cur = con.execute(sql, params)
            return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
        return pd.read_sql_query(sql, con, params=params)


def exists(path=None) -> bool:
    """True if the store has been created and holds at least one lot."""
    t = target(path)
    if not is_pg(t) and not Path(t).exists():
        return False
    try:
        return len(read("SELECT 1 FROM lots LIMIT 1", (), path)) > 0
    except Exception:
        return False


def revision(path=None):
    """Changes whenever the data changes (used by the UI to refresh its cached analysis)."""
    t = target(path)
    if is_pg(t):
        try:
            r = read("SELECT value FROM meta WHERE key='rev'", (), path)
            return r["value"].iloc[0] if len(r) else "0"
        except Exception:
            return "0"
    p = Path(t)
    return p.stat().st_mtime if p.exists() else 0


def get_meta(key: str, path=None) -> str | None:
    r = read("SELECT value FROM meta WHERE key=?", (key,), path)
    return r["value"].iloc[0] if len(r) else None


def set_meta(key: str, value: str, path=None) -> None:
    with connect(path) as con:
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))


def upsert_measurements(df: pd.DataFrame, path=None) -> int:
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


def write_table(name: str, df: pd.DataFrame, path=None, replace: bool = False) -> None:
    cols = list(df.columns)
    rows = df.astype(object).where(df.notna(), None).values.tolist()
    with connect(path) as con:
        if replace:
            con.execute(f"DELETE FROM {name}")
        con.executemany(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)


def log_ingest(file: str, kind: str, profile: str | None, confidence: float | None,
               rows: int, status: str, note: str = "", path=None) -> None:
    with connect(path) as con:
        con.execute(
            "INSERT INTO ingest_log (at, file, kind, profile, confidence, rows, status, note) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), file, kind, profile, confidence, rows, status, note),
        )


def reset(path=None) -> None:
    """Delete all data (SQLite: remove the file; PostgreSQL: drop the tables)."""
    t = target(path)
    if is_pg(t):
        import psycopg

        with psycopg.connect(t) as con:
            for name in _COLS:
                con.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
        _ready.discard(t)
        return
    p = Path(t)
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
