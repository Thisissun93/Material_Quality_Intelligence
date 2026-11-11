"""Find a stored CoA document, render its pages and line its values up against in-house results."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, db

SEARCH = ("inbox", "archive", "demo_inbox")


def locate(name: str, root: Path | None = None) -> Path | None:
    root = root or config.DATA_DIR
    for sub in SEARCH:
        hits = list((root / sub).rglob(name)) if (root / sub).exists() else []
        if hits:
            return hits[0]
    return None


@lru_cache(maxsize=64)
def pages(path: str, scale: float = 1.6) -> list:
    """PDF pages as PIL images (pypdfium2, no system dependency)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    return [pdf[i].render(scale=scale).to_pil() for i in range(len(pdf))]


def documents(dbpath=None) -> pd.DataFrame:
    """One row per CoA file: lot, supplier, number of values, worst gap vs in-house as % of half-spec."""
    m = db.read("SELECT m.lot_id, m.material, m.property, m.value, m.source, m.source_file, l.supplier, l.made_at "
                "FROM measurements m LEFT JOIN lots l ON l.lot_id = m.lot_id "
                "WHERE m.material != 'product'", (), dbpath)
    if m.empty:
        return m
    coa = m[m["source"] == "coa"]
    ih = m[m["source"] == "inhouse"].groupby(["lot_id", "property"])["value"].last()
    specs = config.all_property_specs()
    rows = []
    for f, g in coa.groupby("source_file"):
        worst, oos = np.nan, 0
        for _, r in g.iterrows():
            s = specs.get(r["property"], {})
            if s.get("lsl") is not None and s.get("usl") is not None:
                half = (s["usl"] - s["lsl"]) / 2
                v = ih.get((r["lot_id"], r["property"]))
                if v is not None and not pd.isna(v):
                    gap = abs(r["value"] - v) / half * 100
                    worst = gap if pd.isna(worst) else max(worst, gap)
                    oos += int(v < s["lsl"] or v > s["usl"])
        rows.append({"file": f, "lot_id": g["lot_id"].iloc[0], "supplier": g["supplier"].iloc[0],
                     "material": g["material"].iloc[0], "made_at": g["made_at"].iloc[0], "items": len(g),
                     "gap_pct": worst, "inhouse_oos": oos})
    return pd.DataFrame(rows).sort_values(["made_at", "lot_id"], ascending=[False, True]).reset_index(drop=True)


def compare(file: str, dbpath=None, flag_pct: float = 25.0) -> pd.DataFrame:
    """Values printed on one CoA vs in-house measurement and spec."""
    m = db.read("SELECT lot_id, property, value, unit FROM measurements WHERE source='coa' AND source_file=?",
                (file,), dbpath)
    if m.empty:
        return m
    lot = m["lot_id"].iloc[0]
    ih = db.read("SELECT property, value FROM measurements WHERE source='inhouse' AND lot_id=?", (lot,), dbpath)
    ih = ih.groupby("property")["value"].last()
    specs = config.all_property_specs()
    rows = []
    for _, r in m.iterrows():
        s = specs.get(r["property"], {})
        v = ih.get(r["property"], np.nan)
        lsl, usl = s.get("lsl"), s.get("usl")
        half = (usl - lsl) / 2 if lsl is not None and usl is not None else np.nan
        gap = r["value"] - v if not pd.isna(v) else np.nan
        if pd.isna(v):
            verdict = "no_inhouse"
        elif (lsl is not None and v < lsl) or (usl is not None and v > usl):
            verdict = "inhouse_oos"
        elif not pd.isna(half) and abs(gap) / half * 100 > flag_pct:
            verdict = "gap"
        else:
            verdict = "ok"
        rows.append({"property": r["property"], "unit": s.get("unit", r["unit"]), "coa": r["value"], "inhouse": v,
                     "gap": gap, "gap_pct": abs(gap) / half * 100 if not pd.isna(half) and not pd.isna(gap) else np.nan,
                     "lsl": lsl, "usl": usl, "verdict": verdict})
    return pd.DataFrame(rows)
