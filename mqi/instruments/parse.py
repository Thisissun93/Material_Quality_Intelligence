"""Apply a profile to a file and return canonical records in canonical units."""
from __future__ import annotations

import re
from io import StringIO

import numpy as np
import pandas as pd

from .. import config, units
from .fingerprint import Fingerprint
from .registry import META


def _num(s: pd.Series, decimal: str) -> pd.Series:
    s = s.astype(str).str.strip()
    if decimal == ",":
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    s = s.str.extract(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")[0]
    return pd.to_numeric(s, errors="coerce")


def _dates(s: pd.Series, prof: dict) -> pd.Series:
    """Any instrument date style -> ISO 8601. European '14.05.2026' is day-first."""
    s = s.astype(str).str.strip()
    if prof.get("date_format"):
        d = pd.to_datetime(s, format=prof["date_format"], errors="coerce")
    else:
        dayfirst = bool(s.str.match(r"^\d{1,2}\.\d{1,2}\.\d{4}").any())
        d = pd.to_datetime(s, format="mixed", dayfirst=dayfirst, errors="coerce")
    return d.dt.strftime("%Y-%m-%dT%H:%M:%S").where(d.notna(), None)


def _frame(fp: Fingerprint, prof: dict) -> pd.DataFrame:
    """Raw dataframe with original headers (table/long) or one row (keyvalue)."""
    if prof.get("layout") == "keyvalue":
        return pd.DataFrame([fp.keyvalues])
    key = prof.get("header_row_contains")
    start = fp.header_index or 0
    if key:
        for i, l in enumerate(fp.lines):
            if key.lower() in l.lower():
                start = i
                break
    delim = prof.get("delimiter", fp.delimiter or ",").encode().decode("unicode_escape")
    df = pd.read_csv(StringIO("\n".join(fp.lines[start:])), sep=delim, dtype=str, engine="python")
    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(how="all")
    f = prof.get("filter")
    if f and f["column"] in df.columns:
        df = df[_num(df[f["column"]], prof.get("decimal", ".")) == float(f["equals"])]
    return df


def _col(df: pd.DataFrame, name: str) -> str | None:
    """Exact, then normalised, then fuzzy (>= 90) column lookup. Firmware updates rename headers."""
    from rapidfuzz import fuzz, process

    from .registry import _norm
    if name in df.columns:
        return name
    norm = {_norm(c): c for c in df.columns}
    if _norm(name) in norm:
        return norm[_norm(name)]
    hit = process.extractOne(_norm(name), list(norm), scorer=fuzz.ratio)
    return norm[hit[0]] if hit and hit[1] >= 90 else None


def parse_with(fp: Fingerprint, prof: dict) -> pd.DataFrame:
    """Measurement records: lot_id, property, value (canonical unit), unit, operator, measured_at."""
    df = _frame(fp, prof)
    dec = prof.get("decimal", fp.decimal or ".")
    specs = config.all_property_specs()
    cols = prof.get("columns", {})
    meta = {k: _col(df, v) for k, v in cols.items() if k in META}

    def base() -> pd.DataFrame:
        b = pd.DataFrame(index=df.index)
        for k in ("lot_id", "operator", "measured_at"):
            c = meta.get(k)
            b[k] = df[c].astype(str).str.strip() if c else None
        if prof.get("layout") == "keyvalue" and b["lot_id"].notna().all():
            b["lot_id"] = b["lot_id"].str.split().str[0]
        if b["measured_at"].notna().any():
            b["measured_at"] = _dates(b["measured_at"], prof)
        return b

    out = []
    if prof.get("layout") == "long":
        pcol, vcol = _col(df, prof["property_column"]), _col(df, prof["value_column"])
        ucol = _col(df, prof.get("unit_column", "")) if prof.get("unit_column") else None
        b = base()
        b["property"] = df[pcol].str.strip().map(prof.get("property_map", {}))
        b["raw"] = _num(df[vcol], dec)
        b["src_unit"] = df[ucol].str.strip() if ucol else None
        b = b.dropna(subset=["property"])
        for prop, g in b.groupby("property"):
            canon = specs.get(prop, {}).get("unit")
            g = g.copy()
            g["value"] = [float(units.convert([r], u, canon)[0]) for r, u in zip(g["raw"], g["src_unit"])]
            g["unit"] = canon
            out.append(g)
    else:
        for prop, header in cols.items():
            if prop in META:
                continue
            c = _col(df, header)
            if c is None:
                continue
            canon = specs.get(prop, {}).get("unit")
            src_unit = (prof.get("units") or {}).get(prop) or units.unit_from_header(header)
            raw = _num(df[c], dec)
            if prof.get("layout") == "keyvalue" and not src_unit:
                m = re.search(r"[\d.]+\s*([^\d\s]+)$", str(df[c].iloc[0]))
                src_unit = m.group(1) if m else None
            b = base()
            b["property"] = prop
            b["value"] = units.convert(raw, src_unit, canon)
            b["unit"] = canon
            out.append(b)
    if not out:
        return pd.DataFrame(columns=["lot_id", "property", "value", "unit", "operator", "measured_at"])
    res = pd.concat(out, ignore_index=True)
    res = res[np.isfinite(res["value"].astype(float))]
    res["profile"] = prof["id"]
    res["instrument"] = prof.get("label", prof["id"])
    return res[["lot_id", "property", "value", "unit", "operator", "measured_at", "profile", "instrument"]]


def parse_table(fp: Fingerprint, prof: dict) -> pd.DataFrame:
    """Non-measurement exports (lot master, genealogy, Gage study) renamed to canonical columns."""
    df = _frame(fp, prof)
    ren = {_col(df, v): k for k, v in prof.get("columns", {}).items() if _col(df, v)}
    df = df[list(ren)].rename(columns=ren)
    for k in df.columns:
        if k in config.all_property_specs():
            df[k] = _num(df[k], prof.get("decimal", "."))
    return df
