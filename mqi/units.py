"""Unit normalization to canonical units, plus unit inference from value ranges.

Different instruments report the same quantity in different units
(cP vs mPa·s vs Pa·s, °C vs K, % vs ppm). Everything is converted to the
canonical unit defined in config/specs.yaml before it is stored.
"""
from __future__ import annotations

import re

import numpy as np

# canonical -> {alias: factor or callable}; value_canonical = f(value_source)
_CONVERSIONS: dict[str, dict] = {
    "mPa·s": {"mpa·s": 1, "mpas": 1, "mpa.s": 1, "cp": 1, "centipoise": 1,
              "pa·s": 1000, "pas": 1000, "pa.s": 1000, "p": 100, "poise": 100},
    "°C": {"°c": lambda x: x, "c": lambda x: x, "degc": lambda x: x, "celsius": lambda x: x,
           "k": lambda x: x - 273.15, "kelvin": lambda x: x - 273.15,
           "°f": lambda x: (x - 32) * 5 / 9, "f": lambda x: (x - 32) * 5 / 9},
    "J/g": {"j/g": 1, "jg^-1": 1, "jg-1": 1, "kj/kg": 1, "mj/mg": 1, "cal/g": 4.184},
    "µm": {"µm": 1, "um": 1, "micron": 1, "microns": 1, "nm": 0.001, "mm": 1000},
    "%": {"%": 1, "pct": 1, "percent": 1, "ppm": 1e-4, "wt%": 1},
    "wt%": {"wt%": 1, "%": 1, "pct": 1, "percent": 1},
    "g/eq": {"g/eq": 1, "g/equiv": 1, "eew": 1},
    "mgKOH/g": {"mgkoh/g": 1, "mg koh/g": 1, "mg/g": 1},
}


def _key(unit: str | None) -> str:
    if not unit:
        return ""
    u = unit.strip().lower().replace(" ", "").replace("[", "").replace("]", "")
    return u.replace("μ", "µ").replace("º", "°")


def convert(values, src_unit: str | None, canonical: str):
    """Convert values from src_unit to the canonical unit. Unknown unit -> unchanged."""
    arr = np.asarray(values, dtype=float)
    table = _CONVERSIONS.get(canonical, {})
    k = _key(src_unit)
    if not k or k == _key(canonical):
        return arr
    f = table.get(k)
    if f is None:
        return arr
    return f(arr) if callable(f) else arr * f


def known_units(canonical: str) -> list[str]:
    return list(_CONVERSIONS.get(canonical, {}).keys())


def _all_units() -> set[str]:
    s = set()
    for canon, table in _CONVERSIONS.items():
        s.add(_key(canon))
        s.update(table.keys())
    return s


def unit_from_header(header: str) -> str | None:
    """Pull a unit out of a header like 'Viscosity (cP)', 'Visc[mPa.s]', 'Peak/K' or 'visc_cp'.

    Only returns strings that are real units ('Enthalpy (normalized)' -> None).
    """
    known = _all_units()
    cands = re.findall(r"[\(\[]\s*([^\)\]]+?)\s*[\)\]]", header)
    m = re.search(r"/\s*([^/\s]+)$", header)
    if m:
        cands.append(m.group(1))
    m = re.search(r"_([a-z%µ]+)$", header.lower())
    if m:
        cands.append(m.group(1))
    for c in cands:
        if _key(c) in known:
            return c
    return None


def infer_unit(values, canonical: str, plausible: tuple[float, float] | None) -> tuple[str | None, float]:
    """Choose the source unit that makes the median value physically plausible.

    Returns (unit, score 0..1). Used when a header carries no unit.
    """
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0 or plausible is None:
        return None, 0.0
    lo, hi = plausible
    best, best_score = None, 0.0
    candidates = [canonical] + [u for u in known_units(canonical) if _key(u) != _key(canonical)]
    for u in candidates:  # canonical first, so it wins ties
        conv = convert(arr, u, canonical)
        inside = float(np.mean((conv >= lo) & (conv <= hi)))
        if inside > best_score:
            best, best_score = u, inside
    return best, best_score
