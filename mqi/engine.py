"""Run every analysis once and hand one bundle to the UI, the report and the insight engine."""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config, db
from .analytics import capability, genealogy, msa, spc, supplier

warnings.filterwarnings("ignore", category=RuntimeWarning)

SPC_PROPS = ["viscosity", "tg", "peak_temp", "enthalpy", "filler_pct"]
T2_PROPS = ["viscosity", "tg", "peak_temp", "enthalpy"]


@dataclass
class Bundle:
    product: pd.DataFrame
    raw: pd.DataFrame
    baseline: int
    gage: msa.GageResult | None = None
    gage_data: pd.DataFrame | None = None
    cap: dict = field(default_factory=dict)
    spc: dict = field(default_factory=dict)
    t2: pd.DataFrame | None = None
    trust: pd.DataFrame | None = None
    score: pd.DataFrame | None = None
    table: pd.DataFrame | None = None
    models: dict = field(default_factory=dict)
    attribution: dict = field(default_factory=dict)
    matching: pd.DataFrame | None = None
    dbpath: object = None


def run(dbpath=None, baseline: int | None = None) -> Bundle:
    specs = config.specs()["product"]["properties"]
    rules = config.rules()
    prod = db.product_wide("inhouse", dbpath)
    raw = db.raw_wide(dbpath)
    n = len(prod)
    baseline = baseline or max(20, min(40, n // 3))
    b = Bundle(prod, raw, baseline, dbpath=dbpath)

    g = db.read("SELECT * FROM msa", (), dbpath)
    if not g.empty:
        prop = g["property"].iloc[0]
        s = config.all_property_specs().get(prop, {})
        tol = (s["usl"] - s["lsl"]) if s.get("usl") is not None and s.get("lsl") is not None else None
        b.gage, b.gage_data = msa.gage_rr(g, tolerance=tol), g

    if n:
        for p in SPC_PROPS:
            if p in prod:
                x = prod[p].values
                b.cap[p] = capability.analyze(x, specs[p].get("lsl"), specs[p].get("usl"))
                b.spc[p] = spc.detection_summary(x, baseline, rules["spc"])
        cols = [p for p in T2_PROPS if p in prod]
        if len(cols) >= 2:
            b.t2 = spc.hotelling(prod[cols], baseline, rules["spc"]["t2_alpha"],
                                 rules["spc"].get("t2_window", 30))

    if not raw.empty:
        b.trust = supplier.coa_trust(raw)
        b.score = supplier.scorecard(raw, b.trust)

    try:
        b.table = genealogy.training_table(dbpath)
        if len(b.table) >= 30:
            b.models = genealogy.fit_all(b.table)
            for p, s in b.spc.items():
                i = next((v for v in (s["first"]["EWMA"], s["first"]["Nelson 2-8"], s["first"]["CUSUM"])
                          if v is not None), None)
                if p in b.models and i is not None:
                    lots = prod["lot_id"].iloc[i:]
                    ai = b.table.index[b.table["lot_id"].isin(lots)]
                    bi = b.table.index[b.table["lot_id"].isin(prod["lot_id"].iloc[:baseline])]
                    if len(ai) >= 3:
                        b.attribution[p] = genealogy.attribution(b.table, b.models[p], ai, bi)
            b.matching = genealogy.lot_matching(b.models, dbpath)
    except Exception:
        pass
    return b


def lots_after(b: Bundle, prop: str) -> pd.DataFrame:
    """Product lots from the first early alarm on, with the raw lots they consumed."""
    s = b.spc.get(prop)
    if not s or b.table is None:
        return pd.DataFrame()
    i = next((v for v in (s["first"]["EWMA"], s["first"]["Nelson 2-8"]) if v is not None), None)
    if i is None:
        return pd.DataFrame()
    lots = b.product["lot_id"].iloc[i:]
    return b.table[b.table["lot_id"].isin(lots)]


def supplier_share(b: Bundle, prop: str, material: str = "silica_filler") -> pd.Series:
    """Which supplier fed the alarm lots vs the baseline lots."""
    after = lots_after(b, prop)
    col = f"{material}_supplier"
    if after.empty or col not in after:
        return pd.Series(dtype=float)
    base = b.table[b.table["lot_id"].isin(b.product["lot_id"].iloc[:b.baseline])]
    return pd.DataFrame({"baseline": base[col].value_counts(normalize=True),
                         "alarm": after[col].value_counts(normalize=True)}).fillna(0)


def instrument_shift(prop: str, dbpath=None, b: Bundle | None = None) -> dict | None:
    """Did the property step when the lab changed instruments?

    ANCOVA: value ~ raw-material attributes + new_instrument. The instrument coefficient is the
    bias after removing what the raw materials explain, so a real process drift that happens
    at the same time is not blamed on the instrument (and vice versa).
    """
    import statsmodels.api as sm

    m = db.read("SELECT lot_id, value, instrument, measured_at FROM measurements "
                "WHERE material='product' AND source='inhouse' AND property=? ORDER BY measured_at", (prop,), dbpath)
    if m["instrument"].nunique() < 2:
        return None
    first_use = m.groupby("instrument")["measured_at"].min().sort_values()
    new, old = first_use.index[-1], first_use.index[-2]
    m = m[m["instrument"].isin([new, old])].copy()
    m["new"] = (m["instrument"] == new).astype(float)
    if m["new"].sum() < 5 or (1 - m["new"]).sum() < 5:
        return None
    X = m[["new"]]
    adjusted = False
    if b is not None and b.table is not None:
        from .analytics.genealogy import FEATURES
        feats = [f for f in FEATURES if f in b.table]
        t = b.table.set_index("lot_id")[feats]
        X = X.join(m["lot_id"].map(lambda l: l).to_frame().join(t, on="lot_id")[feats])
        adjusted = True
    d = pd.concat([m[["value"]], X], axis=1).dropna()
    fit = sm.OLS(d["value"], sm.add_constant(d.drop(columns="value"))).fit()
    return {"old": old, "new": new, "since": first_use.iloc[-1][:10], "diff": float(fit.params["new"]),
            "p": float(fit.pvalues["new"]), "first_lot": m[m["instrument"] == new]["lot_id"].iloc[0],
            "sd": float(np.sqrt(fit.scale)), "adjusted": adjusted}


def health(b: Bundle) -> dict:
    """Headline numbers for the overview."""
    specs = config.specs()["product"]["properties"]
    worst = min(b.cap.items(), key=lambda kv: kv[1].ppk) if b.cap else (None, None)
    alarms = sum(1 for s in b.spc.values() if any(v is not None for v in s["first"].values()))
    oos = 0
    for p, s in specs.items():
        if p in b.product:
            x = b.product[p]
            oos += int(((x < (s.get("lsl") if s.get("lsl") is not None else -np.inf)) |
                        (x > (s.get("usl") if s.get("usl") is not None else np.inf))).sum())
    return {"lots": len(b.product), "worst_prop": worst[0], "worst_ppk": worst[1].ppk if worst[1] else None,
            "spc_alarms": alarms, "oos": oos,
            "t2_hidden": int((b.t2["alarm"] & b.t2["univariate_ok"]).sum()) if b.t2 is not None else 0,
            "untrusted": int((b.trust["trust"] == "untrusted").sum()) if b.trust is not None else 0,
            "grr": b.gage.grr_pct_sv if b.gage else None}
