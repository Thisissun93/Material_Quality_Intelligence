"""Supplier Certificate of Analysis (PDF) -> structured values.

Suppliers use different layouts (tables, dotted lines, key: value). Instead of one parser
per supplier, every text line is matched against the shared vocabulary, so a new supplier
template usually works without code changes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pdfplumber
import yaml

from .. import config, units

LOT_RE = re.compile(r"\b((?:ER|SF|HD|UF)-[A-Z0-9]{2,}-?\d{2,})\b")
NUM_RE = re.compile(r"(?<![\w.])([-+]?\d+(?:[.,]\d+)?)(?![\d.])\s*((?:[A-Za-zµμ%°][A-Za-zµμ%°/·\^\-0-9]*)?)")
SUPPLIER_RE = re.compile(r"(?:supplier|manufacturer|issued by|company)\s*[:\-]?\s*([A-Za-z][\w\- ]{2,40})", re.I)


def _vocab_choices() -> dict[str, str]:
    with open(config.CONFIG_DIR / "vocabulary.yaml", encoding="utf-8") as f:
        v = yaml.safe_load(f)
    return {s.lower(): p for p, syns in v["properties"].items() for s in syns}


def pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _line_value(line: str, label_end: int) -> tuple[float | None, str | None]:
    """Last number on the line after the label is the result (spec ranges come earlier)."""
    tail = line[label_end:]
    hits = [m for m in NUM_RE.finditer(tail) if m.group(1)]
    if not hits:
        return None, None
    # results are written after the spec; if a spec range 'a - b' or 'a ~ b' exists, take what follows
    m = hits[-1]
    return float(m.group(1).replace(",", ".")), (m.group(2) or None)


def extract(path: Path | str) -> tuple[pd.DataFrame, dict]:
    """Return (records, info). Records use canonical properties and units."""
    path = Path(path)
    text = pdf_text(path)
    choices = _vocab_choices()
    specs = config.all_property_specs()

    lot = LOT_RE.search(text)
    sup = SUPPLIER_RE.search(text)
    info = {"lot_id": lot.group(1) if lot else None,
            "supplier": sup.group(1).strip() if sup else None, "lines": 0}
    rows = []
    for line in text.splitlines():
        low = line.lower()
        # longest synonym that appears as a whole word ('ash' must not hit 'flash')
        found = []
        for s, p in choices.items():
            m = re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", low)
            if m:
                found.append((s, p, m.end()))
        if not found:
            continue
        syn, prop, end = max(found, key=lambda t: len(t[0]))
        val, unit = _line_value(line, end)
        if val is None or prop not in specs:
            continue
        canon = specs[prop]["unit"]
        unit = unit if unit and units._key(unit) in units._all_units() else None
        v = float(units.convert([val], unit, canon)[0]) if unit else val
        rng = config.plausible_range(prop)
        if not unit and rng and not (rng[0] <= v <= rng[1]):
            u, score = units.infer_unit([val], canon, rng)
            v = float(units.convert([val], u, canon)[0]) if score else v
        rows.append({"lot_id": info["lot_id"], "property": prop, "value": v, "unit": canon})
        info["lines"] += 1
    df = pd.DataFrame(rows).drop_duplicates(subset=["property"], keep="last") if rows else pd.DataFrame(
        columns=["lot_id", "property", "value", "unit"])
    return df, info
