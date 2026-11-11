"""Process capability with the same sigma definitions JMP uses for individual data.

Within sigma  = MR-bar / d2 (d2 = 1.128)      -> Cp, Cpk
Overall sigma = sample standard deviation     -> Pp, Ppk
Non-normal data: Box-Cox transform (specs transformed too) and percentile Ppk.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

D2 = 1.128


@dataclass
class Capability:
    n: int
    mean: float
    sd_within: float
    sd_overall: float
    cp: float | None
    cpk: float
    pp: float | None
    ppk: float
    cpk_ci: tuple[float, float]
    ppm: float
    normal_p: float
    is_normal: bool
    ppk_nonnormal: float | None
    method: str


def _idx(mean, sd, lsl, usl):
    parts = []
    if usl is not None:
        parts.append((usl - mean) / (3 * sd))
    if lsl is not None:
        parts.append((mean - lsl) / (3 * sd))
    return min(parts)


def analyze(x, lsl: float | None, usl: float | None) -> Capability:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    mean = x.mean()
    sd_o = x.std(ddof=1)
    mr = np.abs(np.diff(x))
    sd_w = mr.mean() / D2 if len(mr) else sd_o

    two = lsl is not None and usl is not None
    cp = (usl - lsl) / (6 * sd_w) if two else None
    pp = (usl - lsl) / (6 * sd_o) if two else None
    cpk = _idx(mean, sd_w, lsl, usl)
    ppk = _idx(mean, sd_o, lsl, usl)

    # Bissell (1990) approximate 95% CI for Cpk
    se = np.sqrt(1 / (9 * n) + cpk ** 2 / (2 * (n - 1)))
    ci = (cpk - 1.96 * se, cpk + 1.96 * se)

    ppm = 0.0
    if usl is not None:
        ppm += stats.norm.sf((usl - mean) / sd_o) * 1e6
    if lsl is not None:
        ppm += stats.norm.cdf((lsl - mean) / sd_o) * 1e6

    p_norm = float(stats.shapiro(x).pvalue) if 3 <= n <= 5000 else 1.0
    ppk_nn, method = None, "normal"
    if p_norm < 0.05 and (x > 0).all():
        xt, lam = stats.boxcox(x)
        def tr(v):
            if v is None or v <= 0:
                return None  # Box-Cox undefined at or below zero: that side is not a constraint
            return (v ** lam - 1) / lam if abs(lam) > 1e-6 else np.log(v)
        m_t, s_t = xt.mean(), xt.std(ddof=1)
        lo, hi = tr(lsl), tr(usl)
        ppk_nn = _idx(m_t, s_t, lo, hi) if (lo is not None or hi is not None) else None
        method = f"Box-Cox (λ={lam:.2f})"
    return Capability(n, mean, sd_w, sd_o, cp, cpk, pp, ppk, ci, ppm, p_norm, p_norm >= 0.05, ppk_nn, method)


def rolling(x, lsl, usl, window: int = 25) -> np.ndarray:
    """Ppk in a moving window: shows when capability started to fall."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    for i in range(window - 1, len(x)):
        w = x[i - window + 1: i + 1]
        out[i] = _idx(w.mean(), w.std(ddof=1), lsl, usl)
    return out
