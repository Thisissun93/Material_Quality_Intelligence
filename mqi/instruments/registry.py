"""Instrument registry: search every known export profile, score the match, choose, or learn a new one.

How the choice is made
----------------------
1. Structure score  — signature tokens in the file head, header similarity
                      (fuzzy, so 'Viscosity (cP)' ~ 'Viscosity [cP]'), delimiter / decimal match.
2. Content score    — parse with that profile and check every mapped value is physically
                      plausible after unit conversion (a DSC file parsed as a viscometer fails here).
3. Decision         — auto-accept when confidence and the margin to the runner-up are high;
                      otherwise ask the user. Unknown formats get a draft mapping built from
                      config/vocabulary.yaml, which the user confirms once and saves as a profile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from rapidfuzz import fuzz, process

from .. import config, units
from .fingerprint import Fingerprint, fingerprint

META = {"lot_id", "operator", "measured_at", "part", "replicate", "material", "supplier",
        "made_at", "product_lot", "epoxy_resin", "silica_filler", "hardener",
        "mixer", "mix_time", "vacuum", "degas_temp", "batch_kg", "change_no", "date", "category", "item",
        "description", "owner", "notified"}

TABLE_CATS = ("master", "genealogy", "msa", "process", "changes")
AUTO_ACCEPT = 0.80
MARGIN = 0.08


@dataclass
class Match:
    profile: dict
    structure: float
    content: float
    detail: dict = field(default_factory=dict)

    @property
    def confidence(self) -> float:
        return round(0.7 * self.structure + 0.3 * self.content, 3)

    @property
    def id(self) -> str:
        return self.profile["id"]


def load_profiles(directory: Path | None = None) -> list[dict]:
    d = Path(directory or config.INSTRUMENT_DIR)
    out = []
    for p in sorted(d.glob("*.yaml")):
        with open(p, encoding="utf-8") as f:
            prof = yaml.safe_load(f)
        prof["_path"] = str(p)
        out.append(prof)
    return out


def _norm(s: str) -> str:
    s = s.lower().replace("μ", "µ").replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    return re.sub(r"\s+", " ", s).strip()


def _structure_score(fp: Fingerprint, prof: dict) -> tuple[float, dict]:
    head = _norm(fp.head)
    sig = prof.get("signature") or []
    s_sig = np.mean([_norm(t) in head for t in sig]) if sig else 0.5

    expected = [v for v in prof.get("columns", {}).values()]
    if prof.get("layout") == "long":
        expected += [prof["property_column"], prof["value_column"]]
    if prof.get("layout") == "keyvalue":
        found = list(fp.keyvalues.keys())
    else:
        found = fp.headers
    if expected and found:
        ratios = [process.extractOne(_norm(e), [_norm(f) for f in found], scorer=fuzz.ratio)[1] / 100
                  for e in expected]
        s_cols = float(np.mean(ratios))
    else:
        s_cols = 0.0

    if prof.get("layout", "table") != fp.layout:
        s_fmt = 0.0
    elif fp.layout == "table":
        d = prof.get("delimiter", ",").encode().decode("unicode_escape")
        s_fmt = 0.5 * (d == fp.delimiter) + 0.5 * (prof.get("decimal", ".") == fp.decimal)
    else:
        s_fmt = 1.0
    score = 0.35 * s_sig + 0.45 * s_cols + 0.20 * s_fmt
    return float(score), {"signature": round(float(s_sig), 2), "columns": round(s_cols, 2),
                          "format": round(float(s_fmt), 2)}


def _content_score(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    vals = []
    for prop, g in df.groupby("property"):
        rng = config.plausible_range(prop)
        if rng:
            v = g["value"].astype(float)
            vals.append(float(((v >= rng[0]) & (v <= rng[1])).mean()))
    return float(np.mean(vals)) if vals else 0.5


def rank(path: Path | str, profiles: list[dict] | None = None) -> tuple[Fingerprint, list[Match]]:
    """Score every profile against the file, best first."""
    from .parse import parse_with  # local import avoids a cycle

    fp = fingerprint(path)
    profiles = profiles or load_profiles()
    matches = []
    for prof in profiles:
        s, detail = _structure_score(fp, prof)
        c = 0.0
        if s >= 0.35 and prof.get("category") not in TABLE_CATS:
            try:
                c = _content_score(parse_with(fp, prof))
            except Exception:
                c = 0.0
        elif prof.get("category") in TABLE_CATS:
            c = 1.0 if s >= 0.6 else 0.0
        matches.append(Match(prof, s, c, detail))
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return fp, matches


def decide(matches: list[Match]) -> tuple[str, Match | None]:
    """'auto' | 'review' | 'unknown' with the chosen match."""
    if not matches or matches[0].confidence < 0.55:
        return "unknown", None
    top = matches[0]
    margin = top.confidence - (matches[1].confidence if len(matches) > 1 else 0)
    if top.confidence >= AUTO_ACCEPT and margin >= MARGIN:
        return "auto", top
    return "review", top


# ---- learning a new format ---------------------------------------------------

def _vocab() -> dict:
    with open(config.CONFIG_DIR / "vocabulary.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _strip_unit(header: str) -> str:
    h = re.sub(r"[\(\[].*?[\)\]]", "", header)
    h = re.sub(r"/[^/]*$", "", h)
    h = re.sub(r"_(cp|mpas|pas|k|c|um|ppm|pct)$", "", h, flags=re.I)
    return _norm(h.replace("_", " "))


def propose_mapping(fp: Fingerprint) -> list[dict]:
    """Draft a column -> field mapping for an unknown file. Each row carries its own confidence."""
    vocab = _vocab()
    choices: dict[str, str] = {}
    for fld, syns in {**vocab["fields"], **vocab["properties"]}.items():
        for s in syns:
            choices[_norm(s)] = fld
    for s in vocab.get("ignore", []):
        choices[_norm(s)] = "__ignore__"

    headers = fp.headers if fp.layout == "table" else list(fp.keyvalues.keys())
    body = _table_body(fp) if fp.layout == "table" else None
    rows = []
    for h in headers:
        key = _strip_unit(h)
        best = process.extractOne(key, list(choices.keys()), scorer=fuzz.WRatio)
        fld, sim = (choices[best[0]], best[1] / 100) if best else (None, 0.0)
        unit = units.unit_from_header(h)
        unit_conf = 1.0 if unit else 0.0
        if fld and fld not in vocab["fields"] and fld != "__ignore__":
            canon = config.all_property_specs().get(fld, {}).get("unit")
            sample = (pd.to_numeric(body[h].astype(str).str.replace(",", ".") if fp.decimal == ","
                                    else body[h], errors="coerce") if body is not None
                      else pd.to_numeric(pd.Series([re.split(r"\s+", fp.keyvalues[h])[0]]), errors="coerce"))
            if not unit:
                unit, unit_conf = units.infer_unit(sample, canon, config.plausible_range(fld))
            conv = units.convert(sample.dropna(), unit, canon)
            rng = config.plausible_range(fld)
            plaus = float(np.mean((conv >= rng[0]) & (conv <= rng[1]))) if rng and len(conv) else 0.0
            conf = round(0.6 * sim + 0.4 * plaus, 2)
        else:
            conf = round(sim, 2)
        rows.append({"column": h, "field": None if fld == "__ignore__" else fld,
                     "unit": unit, "confidence": conf})
    # keep only the best column per field
    seen: dict[str, int] = {}
    for i, r in enumerate(rows):
        f = r["field"]
        if not f:
            continue
        if f in seen and rows[seen[f]]["confidence"] >= r["confidence"]:
            r["field"] = None
        else:
            if f in seen:
                rows[seen[f]]["field"] = None
            seen[f] = i
    return rows


def _table_body(fp: Fingerprint) -> pd.DataFrame:
    from io import StringIO
    body = "\n".join(fp.lines[fp.header_index:])
    return pd.read_csv(StringIO(body), sep=fp.delimiter, dtype=str, engine="python")


def save_profile(fp: Fingerprint, mapping: list[dict], label: str, category: str,
                 directory: Path | None = None) -> Path:
    """Persist a confirmed mapping so the same format is recognised automatically next time."""
    d = Path(directory or config.INSTRUMENT_DIR)
    pid = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "learned"
    cols = {m["field"]: m["column"] for m in mapping if m.get("field")}
    unit_over = {m["field"]: m["unit"] for m in mapping
                 if m.get("field") and m.get("unit") and not units.unit_from_header(m["column"])}
    head_tokens = [l.strip() for l in fp.lines[: (fp.header_index or 0)] if l.strip()][:2]
    prof = {"id": pid, "label": label, "category": category, "layout": fp.layout,
            "learned": True, "columns": cols,
            "signature": [t.split(fp.delimiter or ":")[-1].strip() for t in head_tokens] or [],}
    if fp.layout == "table":
        prof |= {"delimiter": fp.delimiter if fp.delimiter != "\t" else "\\t",
                 "decimal": fp.decimal, "header_row_contains": fp.headers[0]}
    if unit_over:
        prof["units"] = unit_over
    path = d / f"{pid}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(prof, f, allow_unicode=True, sort_keys=False)
    return path
