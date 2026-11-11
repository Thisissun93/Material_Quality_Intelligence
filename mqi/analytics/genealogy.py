"""Raw material -> product: explain past alarms and predict the next batch before it is made.

1. Join every product lot to the in-house values of the raw lots it consumed (MES genealogy).
2. Fit one model per product property (ridge vs gradient boosting, chosen by cross-validation).
3. Prediction uncertainty from out-of-fold residuals -> probability of being out of spec.
4. Root cause: which raw attributes moved the alarm lots away from the baseline lots.
5. Lot matching: score every combination of available raw lots and rank them by risk.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .. import config, db

FEATURES = ["eew", "resin_visc", "d50", "moisture", "amine_value"]
TARGETS = ["viscosity", "tg", "peak_temp", "enthalpy"]


def raw_features(dbpath=None, source: str = "inhouse") -> pd.DataFrame:
    """Raw lot -> attribute values. In-house first, CoA used only where in-house is missing."""
    m = db.read("SELECT lot_id, property, source, value FROM measurements WHERE material!='product'", (), dbpath)
    w = m.pivot_table(index=["lot_id", "property"], columns="source", values="value", aggfunc="last")
    pref = w.get(source)
    alt = w.get("coa" if source == "inhouse" else "inhouse")
    val = pref.combine_first(alt) if pref is not None and alt is not None else (pref if pref is not None else alt)
    return val.rename("value").reset_index().pivot(index="lot_id", columns="property", values="value")


def training_table(dbpath=None) -> pd.DataFrame:
    prod = db.product_wide("inhouse", dbpath)
    gen = db.read("SELECT product_lot, material, raw_lot FROM genealogy", (), dbpath)
    rf = raw_features(dbpath)
    lots = db.read("SELECT lot_id, supplier FROM lots", (), dbpath).set_index("lot_id")["supplier"]
    g = gen.pivot(index="product_lot", columns="material", values="raw_lot")
    feats = pd.DataFrame(index=g.index)
    for mat in g.columns:
        sub = rf.reindex(g[mat])
        sub.index = g.index
        for c in sub.columns:
            if c in FEATURES and sub[c].notna().any():
                feats[c] = sub[c]
        feats[f"{mat}_lot"] = g[mat]
        feats[f"{mat}_supplier"] = g[mat].map(lots)
    return prod.set_index("lot_id").join(feats, how="inner").reset_index()


@dataclass
class PropertyModel:
    target: str
    kind: str
    model: object
    r2: float
    mae: float
    resid_sd: float
    features: list[str]
    importance: pd.Series


def fit(table: pd.DataFrame, target: str, features=FEATURES) -> PropertyModel:
    feats = [f for f in features if f in table]
    d = table.dropna(subset=feats + [target])
    X, y = d[feats].values, d[target].values
    cv = KFold(5, shuffle=True, random_state=0)
    cands = {
        "ridge": make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-3, 3, 25))),
        "gbm": GradientBoostingRegressor(n_estimators=250, max_depth=2, learning_rate=0.05,
                                         subsample=0.8, random_state=0),
    }
    best = None
    for kind, mdl in cands.items():
        pred = cross_val_predict(mdl, X, y, cv=cv)
        res = y - pred
        mae = float(np.mean(np.abs(res)))
        r2 = float(1 - np.sum(res ** 2) / np.sum((y - y.mean()) ** 2))
        if best is None or mae < best[2]:
            best = (kind, mdl, mae, r2, float(np.std(res, ddof=1)))
    kind, mdl, mae, r2, sd = best
    mdl.fit(X, y)
    # importance = std of each feature's effect on predictions (one-at-a-time, model-agnostic)
    base = np.median(X, axis=0)
    imp = {}
    for j, f in enumerate(feats):
        grid = np.tile(base, (len(X), 1))
        grid[:, j] = X[:, j]
        imp[f] = float(np.std(mdl.predict(grid)))
    return PropertyModel(target, kind, mdl, r2, mae, sd, feats,
                         pd.Series(imp).sort_values(ascending=False))


def fit_all(table: pd.DataFrame) -> dict[str, PropertyModel]:
    return {t: fit(table, t) for t in TARGETS if t in table}


def p_out_of_spec(mean: np.ndarray, sd: float, lsl, usl) -> np.ndarray:
    p = np.zeros_like(mean, dtype=float)
    if usl is not None:
        p += stats.norm.sf((usl - mean) / sd)
    if lsl is not None:
        p += stats.norm.cdf((lsl - mean) / sd)
    return p


def predict(models: dict[str, PropertyModel], X: pd.DataFrame) -> pd.DataFrame:
    specs = config.specs()["product"]["properties"]
    out = pd.DataFrame(index=X.index)
    risk = np.zeros(len(X))
    for t, m in models.items():
        mu = m.model.predict(X[m.features].values)
        s = specs[t]
        p = p_out_of_spec(mu, m.resid_sd, s.get("lsl"), s.get("usl"))
        out[t] = mu
        out[f"p_oos_{t}"] = p
        risk = 1 - (1 - risk) * (1 - p)
    out["risk"] = risk
    return out


def attribution(table: pd.DataFrame, model: PropertyModel, alarm_idx, baseline_idx) -> pd.DataFrame:
    """How much each raw attribute moved the predicted property between baseline and alarm lots."""
    Xa = table.loc[alarm_idx, model.features]
    Xb = table.loc[baseline_idx, model.features]
    ref = Xb.median().values
    rows = []
    pa = model.model.predict(np.tile(ref, (1, 1)))[0]
    for j, f in enumerate(model.features):
        x = ref.copy()
        x[j] = Xa[f].median()
        rows.append({"feature": f, "baseline": ref[j], "alarm": x[j],
                     "effect": float(model.model.predict(x.reshape(1, -1))[0] - pa)})
    return pd.DataFrame(rows).sort_values("effect", key=np.abs, ascending=False)


def lot_matching(models: dict[str, PropertyModel], dbpath=None, recent_days: int = 45,
                 table: pd.DataFrame | None = None) -> pd.DataFrame:
    """Rank every combination of recently received raw lots by predicted risk."""
    rf = raw_features(dbpath)
    lots = db.read("SELECT lot_id, material, supplier, made_at FROM lots WHERE material!='product'", (), dbpath)
    lots["made_at"] = pd.to_datetime(lots["made_at"])
    cutoff = lots["made_at"].max() - pd.Timedelta(days=recent_days)
    recent = lots[lots["made_at"] >= cutoff]
    groups = [recent[recent["material"] == m]["lot_id"].tolist()
              for m in ("epoxy_resin", "silica_filler", "hardener")]
    combos = list(itertools.product(*groups))
    rows = []
    for r, f, h in combos:
        feat = {}
        for lot in (r, f, h):
            if lot in rf.index:
                feat.update({k: v for k, v in rf.loc[lot].items() if k in FEATURES and pd.notna(v)})
        rows.append({"resin_lot": r, "filler_lot": f, "hardener_lot": h, **feat})
    X = pd.DataFrame(rows).dropna(subset=FEATURES)
    pred = predict(models, X)
    sup = lots.set_index("lot_id")["supplier"]
    res = X.join(pred)
    res["filler_supplier"] = res["filler_lot"].map(sup)
    res["resin_supplier"] = res["resin_lot"].map(sup)
    return res.sort_values("risk").reset_index(drop=True)
