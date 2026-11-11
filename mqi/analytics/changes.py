"""Process records and 4M change log: are they behind a shift?

Two screens used by the 8D (D2 'When' and the 6M table):
  * process_screen  every MES parameter vs the model residual (what raw materials do not explain)
                    and vs the shift period, so Machine / Method can be judged on data
  * changes_near    4M changes around the start of a shift, each with a data check
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .. import db

PARAMS = {"mixer": ("믹서", "Mixer"), "mix_time": ("믹싱 시간", "Mix time"), "vacuum": ("탈포 진공도", "Vacuum"),
          "degas_temp": ("탈포 온도", "Degas temp"), "batch_kg": ("배치 크기", "Batch size")}
UNITS = {"mix_time": "min", "vacuum": "mbar", "degas_temp": "°C", "batch_kg": "kg"}


def process_wide(dbpath=None) -> pd.DataFrame:
    p = db.read("SELECT lot_id, param, value, text FROM process", (), dbpath)
    if p.empty:
        return pd.DataFrame(columns=["lot_id"])
    p["v"] = p["value"].where(p["value"].notna(), p["text"])
    w = p.pivot(index="lot_id", columns="param", values="v").reset_index()
    for c in w.columns:
        if c not in ("lot_id", "mixer"):
            w[c] = pd.to_numeric(w[c], errors="coerce")
    return w


def _residual(b, prop: str) -> pd.Series:
    m = b.models.get(prop)
    t = b.table.set_index("lot_id")
    if m is None:
        return t[prop] - t[prop].mean()
    X = t[m.features].dropna()
    return (t.loc[X.index, prop] - m.model.predict(X.values)).rename("resid")


def process_screen(b, prop: str, alarm_i: int | None) -> pd.DataFrame:
    """One row per process parameter: link to unexplained variation and change in the shift period."""
    w = process_wide(b.dbpath)
    if len(w) <= 1:
        return pd.DataFrame()
    res = _residual(b, prop)
    lots = b.product["lot_id"].tolist()
    period = pd.Series(["base" if i < b.baseline else "alarm" if alarm_i is not None and i >= alarm_i else "mid"
                        for i in range(len(lots))], index=lots)
    d = w.set_index("lot_id").join(res, how="inner").join(period.rename("period"))
    rows = []
    for k in [c for c in d.columns if c in PARAMS]:
        if k == "mixer":
            groups = [g["resid"].values for _, g in d.groupby(k)]
            p_res = stats.f_oneway(*groups).pvalue if len(groups) > 1 else np.nan
            eff = d.groupby(k)["resid"].mean()
            effect = eff.max() - eff.min()
            share = d.groupby("period")[k].agg(lambda s: (s == s.mode().iloc[0]).mean())
            p_shift = stats.chi2_contingency(pd.crosstab(d["period"].isin(["alarm"]), d[k]))[1] \
                if d[k].nunique() > 1 else np.nan
            detail = ", ".join(f"{i} {v:+.3g}" for i, v in eff.items())
        else:
            x = d[[k, "resid"]].dropna()
            r, p_res = stats.pearsonr(x[k], x["resid"]) if x[k].std() > 0 else (0, 1)
            slope = np.polyfit(x[k], x["resid"], 1)[0] if x[k].std() > 0 else 0
            effect = slope * 2 * x[k].std()
            a, bse = d.loc[d["period"] == "alarm", k], d.loc[d["period"] == "base", k]
            p_shift = stats.ttest_ind(a, bse, equal_var=False).pvalue if len(a) > 2 and len(bse) > 2 else np.nan
            detail = f"r={r:+.2f}"
        rows.append(dict(param=k, p_resid=p_res, effect=effect, p_shift=p_shift, detail=detail,
                         linked=bool(p_res < 0.01)))
    return pd.DataFrame(rows)


def changes_near(b, prop: str, start: str, before: int = 45, after: int = 10) -> pd.DataFrame:
    """4M changes in [start - before, start + after] days, each with the property step at that date."""
    c = db.read("SELECT * FROM changes", (), b.dbpath)
    if c.empty:
        return c
    c["date"] = pd.to_datetime(c["date"])
    s0 = pd.to_datetime(start)
    c = c[(c["date"] >= s0 - pd.Timedelta(days=before)) & (c["date"] <= s0 + pd.Timedelta(days=after))].copy()
    if c.empty:
        return c
    x = b.product.set_index(pd.to_datetime(b.product["measured_at"]))[prop]
    res = _residual(b, prop)
    r = pd.Series(res.values, index=pd.to_datetime(b.table.set_index("lot_id").loc[res.index, "measured_at"]))
    steps, rsteps, ps = [], [], []
    for d in c["date"]:
        pre, post = x[x.index < d].tail(15), x[x.index >= d].head(15)
        rpre, rpost = r[r.index < d].tail(15), r[r.index >= d].head(15)
        steps.append(post.mean() - pre.mean() if len(pre) > 2 and len(post) > 2 else np.nan)
        rsteps.append(rpost.mean() - rpre.mean() if len(rpre) > 2 and len(rpost) > 2 else np.nan)
        ps.append(stats.ttest_ind(rpost, rpre, equal_var=False).pvalue if len(rpre) > 2 and len(rpost) > 2 else np.nan)
    c["step"], c["resid_step"], c["p"] = steps, rsteps, ps
    c["days_from_start"] = (c["date"] - s0).dt.days
    return c.sort_values("date")
