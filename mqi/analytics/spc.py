"""Control charts that look ahead, not just at limits.

* Individuals chart with all eight Nelson rules (limits frozen on a baseline window).
* EWMA and tabular CUSUM: catch small sustained shifts several lots earlier than 3-sigma limits.
* Hotelling T²: lots whose single values are all in control but whose combination is not,
  with a per-property contribution so the alarm explains itself.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

D2 = 1.128

NELSON = {
    1: "1 point beyond 3σ",
    2: "9 points in a row on one side",
    3: "6 points steadily increasing or decreasing",
    4: "14 points alternating up and down",
    5: "2 of 3 points beyond 2σ, same side",
    6: "4 of 5 points beyond 1σ, same side",
    7: "15 points within 1σ (stratification)",
    8: "8 points beyond 1σ, both sides",
}


@dataclass
class Limits:
    center: float
    sigma: float

    @property
    def ucl(self):
        return self.center + 3 * self.sigma

    @property
    def lcl(self):
        return self.center - 3 * self.sigma


def baseline_limits(x, baseline: int | None = None, sigma: str = "overall") -> Limits:
    """Limits from the baseline window.

    sigma='overall' (default) treats lot-to-lot raw-material variation as common cause, which is
    the honest choice for batch chemistry; 'moving_range' is the classic I-MR estimate and will
    over-alarm when consecutive lots share the same raw lot.
    """
    x = np.asarray(x, dtype=float)
    b = x[:baseline] if baseline else x
    s = np.abs(np.diff(b)).mean() / D2 if sigma == "moving_range" else b.std(ddof=1)
    return Limits(float(b.mean()), float(s))


def nelson(x, lim: Limits, rules=(1, 2, 3, 4, 5, 6, 7, 8)) -> pd.DataFrame:
    """Boolean matrix: row per point, column per rule; True marks the point that completes the pattern."""
    x = np.asarray(x, dtype=float)
    z = (x - lim.center) / lim.sigma
    n = len(x)
    out = pd.DataFrame(False, index=range(n), columns=list(rules))
    side = np.sign(z)
    for i in range(n):
        if 1 in rules:
            out.loc[i, 1] = abs(z[i]) > 3
        if 2 in rules and i >= 8:
            w = side[i - 8: i + 1]
            out.loc[i, 2] = (w > 0).all() or (w < 0).all()
        if 3 in rules and i >= 5:
            d = np.diff(x[i - 5: i + 1])
            out.loc[i, 3] = (d > 0).all() or (d < 0).all()
        if 4 in rules and i >= 13:
            d = np.sign(np.diff(x[i - 13: i + 1]))
            out.loc[i, 4] = (d[1:] * d[:-1] < 0).all()
        if 5 in rules and i >= 2:
            w = z[i - 2: i + 1]
            out.loc[i, 5] = (w > 2).sum() >= 2 or (w < -2).sum() >= 2
        if 6 in rules and i >= 4:
            w = z[i - 4: i + 1]
            out.loc[i, 6] = (w > 1).sum() >= 4 or (w < -1).sum() >= 4
        if 7 in rules and i >= 14:
            out.loc[i, 7] = (np.abs(z[i - 14: i + 1]) < 1).all()
        if 8 in rules and i >= 7:
            w = z[i - 7: i + 1]
            out.loc[i, 8] = (np.abs(w) > 1).all() and (w > 1).any() and (w < -1).any()
    return out


def ewma(x, lim: Limits, lam: float = 0.2, L: float = 2.7) -> pd.DataFrame:
    x = np.asarray(x, dtype=float)
    z = np.empty_like(x)
    prev = lim.center
    for i, v in enumerate(x):
        prev = lam * v + (1 - lam) * prev
        z[i] = prev
    t = np.arange(1, len(x) + 1)
    w = L * lim.sigma * np.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * t)))
    return pd.DataFrame({"ewma": z, "ucl": lim.center + w, "lcl": lim.center - w,
                         "alarm": (z > lim.center + w) | (z < lim.center - w)})


def cusum(x, lim: Limits, k: float = 0.5, h: float = 4.0) -> pd.DataFrame:
    x = np.asarray(x, dtype=float)
    hi = lo = 0.0
    H, L = [], []
    for v in x:
        hi = max(0.0, hi + (v - lim.center) / lim.sigma - k)
        lo = max(0.0, lo - (v - lim.center) / lim.sigma - k)
        H.append(hi)
        L.append(lo)
    H, L = np.array(H), np.array(L)
    return pd.DataFrame({"cusum_hi": H, "cusum_lo": L, "h": h, "alarm": (H > h) | (L > h)})


def first(mask) -> int | None:
    idx = np.flatnonzero(np.asarray(mask))
    return int(idx[0]) if len(idx) else None


def detection_summary(x, baseline: int, cfg: dict) -> dict:
    """When does each method first alarm after the baseline? Lead time vs the 3σ rule."""
    lim = baseline_limits(x, baseline, cfg.get("sigma", "overall"))
    nl = nelson(x, lim, cfg.get("nelson_rules", range(1, 9)))
    ew = ewma(x, lim, cfg.get("ewma_lambda", 0.2), cfg.get("ewma_L", 2.7))
    cu = cusum(x, lim, cfg.get("cusum_k", 0.5), cfg.get("cusum_h", 4.0))
    after = np.arange(len(x)) >= baseline
    res = {
        "3σ (rule 1)": first(nl[1].values & after) if 1 in nl else None,
        # pattern windows (up to 15 points) must lie fully after the baseline
        "Nelson 2-8": first(nl.drop(columns=[1], errors="ignore").any(axis=1).values
                            & (np.arange(len(x)) >= baseline + 8)),
        "EWMA": first(ew["alarm"].values & after),
        "CUSUM": first(cu["alarm"].values & after),
    }
    return {"limits": lim, "nelson": nl, "ewma": ew, "cusum": cu, "first": res}


# ---- multivariate ---------------------------------------------------------------

def hotelling(X: pd.DataFrame, baseline: int, alpha: float = 0.01, window: int | None = 30) -> pd.DataFrame:
    """Phase II T² with per-variable contributions.

    window=None : reference = fixed baseline (detects any departure, including slow drift).
    window=30   : reference = the previous 30 lots (detects sudden abnormal combinations even while
                  the process is drifting, which EWMA already covers).
    """
    Xv = X.values.astype(float)
    n, p = Xv.shape
    t2 = np.full(n, np.nan)
    contrib = np.full((n, p), np.nan)
    zmax = np.full(n, np.nan)
    ucl = np.nan
    for i in range(n):
        if window:
            if i < window:
                continue
            ref = Xv[i - window:i]
        else:
            ref = Xv[:baseline]
        mu, S = ref.mean(axis=0), np.cov(ref, rowvar=False)
        Si = np.linalg.pinv(S)
        d = Xv[i] - mu
        t2[i] = d @ Si @ d
        contrib[i] = d * (Si @ d)
        zmax[i] = np.max(np.abs(d / ref.std(axis=0, ddof=1)))
        m = len(ref)
        ucl = p * (m + 1) * (m - 1) / (m * (m - p)) * stats.f.ppf(1 - alpha, p, m - p)
    out = pd.DataFrame(contrib, columns=[f"c_{c}" for c in X.columns], index=X.index)
    out.insert(0, "ucl", ucl)
    out.insert(0, "t2", t2)
    out["alarm"] = out["t2"] > ucl
    out["univariate_ok"] = zmax < 3
    return out
