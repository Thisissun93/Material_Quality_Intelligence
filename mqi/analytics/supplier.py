"""Supplier quality: can we trust the CoA, who is drifting, and how hard should we inspect?"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .. import config
from . import capability, spc


def paired(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per raw lot × property with CoA and in-house values side by side."""
    w = raw.pivot_table(index=["lot_id", "material", "supplier", "made_at", "property"],
                        columns="source", values="value", aggfunc="last").reset_index()
    for c in ("coa", "inhouse"):
        if c not in w:
            w[c] = np.nan
    return w.sort_values(["supplier", "property", "made_at"])


def coa_trust(raw: pd.DataFrame, alpha: float | None = None, practical: float | None = None) -> pd.DataFrame:
    """Paired comparison CoA vs in-house per supplier × property.

    trust = 'trusted'   no significant bias and bias < practical share of the spec window
            'verify'    statistically significant but practically small, or n < 5
            'untrusted' bias is significant and large, or the gap is growing (CUSUM on the difference)
    """
    cfg = config.rules()["supplier"]
    alpha = alpha or cfg["coa_bias_alpha"]
    practical = practical or cfg["coa_bias_practical"]
    specs = config.all_property_specs()
    w = paired(raw).dropna(subset=["coa", "inhouse"])
    rows = []
    for (sup, prop), g in w.groupby(["supplier", "property"]):
        d = (g["coa"] - g["inhouse"]).values
        n = len(d)
        s = specs.get(prop, {})
        half = (s.get("usl", np.nan) - s.get("lsl", np.nan)) / 2 if s.get("usl") is not None else np.nan
        bias = d.mean()
        p = stats.ttest_1samp(d, 0).pvalue if n >= 3 and d.std() > 0 else 1.0
        corr = np.corrcoef(g["coa"], g["inhouse"])[0, 1] if n >= 3 else np.nan
        loa = 1.96 * d.std(ddof=1) if n >= 2 else np.nan
        rel = abs(bias) / half if half and np.isfinite(half) else np.nan
        # is the gap growing? significant trend in (CoA - in-house) that has become practically large
        since = None
        if n >= 8 and np.isfinite(half):
            order = np.arange(n)
            lr = stats.linregress(order, d)
            fitted = lr.intercept + lr.slope * order
            if lr.pvalue < 0.01 and abs(fitted[-1] - fitted[0]) > practical * half:
                ref = d[: max(4, n // 3)].mean()
                hit = np.flatnonzero(np.abs(d - ref) > practical * half)
                since = g["lot_id"].iloc[hit[0]] if len(hit) else None
        if n < 5:
            trust = "verify"
        elif (p < alpha and rel > practical) or since:
            trust = "untrusted"
        elif p < alpha:
            trust = "verify"
        else:
            trust = "trusted"
        rows.append(dict(supplier=sup, property=prop, n=n, bias=bias, bias_pct_of_halfspec=100 * rel,
                         p_value=p, corr=corr, loa=loa, drift_since=since, trust=trust))
    return pd.DataFrame(rows)


def scorecard(raw: pd.DataFrame, trust: pd.DataFrame) -> pd.DataFrame:
    """Per supplier: in-house capability, out-of-spec lots, CoA trust and the inspection level to use."""
    cfg = config.rules()["supplier"]
    specs = config.all_property_specs()
    w = paired(raw)
    rows = []
    for sup, g in w.groupby("supplier"):
        ppks, oos, drift = {}, 0, []
        for prop, h in g.groupby("property"):
            s = specs.get(prop, {})
            x = h["inhouse"].dropna().values
            if len(x) < 4:
                continue
            c = capability.analyze(x, s.get("lsl"), s.get("usl"))
            ppks[prop] = c.ppk
            lo = s.get("lsl") if s.get("lsl") is not None else -np.inf
            hi = s.get("usl") if s.get("usl") is not None else np.inf
            oos += int(((x < lo) | (x > hi)).sum())
            # in-house drift within this supplier's own history: recent third vs earlier lots
            k = max(3, len(x) // 3)
            if len(x) >= 8 and stats.ttest_ind(x[-k:], x[:-k], equal_var=False).pvalue < 0.01:
                drift.append(prop)
        min_ppk = min(ppks.values()) if ppks else np.nan
        t = trust[trust["supplier"] == sup]
        worst_trust = ("untrusted" if (t["trust"] == "untrusted").any()
                       else "verify" if (t["trust"] == "verify").any() else "trusted")
        if worst_trust == "untrusted" or drift or oos or min_ppk < 1.0:
            level = "tightened"
        elif worst_trust == "trusted" and min_ppk >= cfg["skip_lot_ok_ppk"]:
            level = "skip-lot"
        else:
            level = "normal"
        rows.append(dict(supplier=sup, material=g["material"].iloc[0], lots=g["lot_id"].nunique(),
                         min_ppk=min_ppk, worst_property=min(ppks, key=ppks.get) if ppks else None,
                         oos_lots=oos, drifting=", ".join(drift) or "-", coa_trust=worst_trust,
                         inspection=level))
    return pd.DataFrame(rows).sort_values(["inspection", "min_ppk"], ascending=[False, True])
