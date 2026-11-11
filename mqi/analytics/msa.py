"""Crossed Gage R&R by two-way ANOVA (AIAG MSA 4th ed.).

Default model keeps the Part × Operator interaction (same as JMP 'Gauge R&R' with
the crossed model). Set pool_alpha=0.25 to reproduce Minitab's practice of pooling a
non-significant interaction into error.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class GageResult:
    anova: pd.DataFrame
    components: pd.DataFrame
    grr_pct_sv: float
    grr_pct_tol: float | None
    ndc: int
    dominant: str              # repeatability | reproducibility
    interaction_kept: bool
    operator_bias: pd.DataFrame = field(default_factory=pd.DataFrame)
    k: float = 6.0


def gage_rr(df: pd.DataFrame, part="part", operator="operator", value="value",
            tolerance: float | None = None, k: float = 6.0, pool_alpha: float | None = None) -> GageResult:
    d = df[[part, operator, value]].dropna().copy()
    d[value] = d[value].astype(float)
    p, o = d[part].nunique(), d[operator].nunique()
    r = d.groupby([part, operator]).size().min()
    n = len(d)
    gm = d[value].mean()

    pm = d.groupby(part)[value].mean()
    om = d.groupby(operator)[value].mean()
    cm = d.groupby([part, operator])[value].mean()

    ss_p = o * r * ((pm - gm) ** 2).sum()
    ss_o = p * r * ((om - gm) ** 2).sum()
    ss_t = ((d[value] - gm) ** 2).sum()
    ss_cells = r * ((cm - gm) ** 2).sum()
    ss_po = ss_cells - ss_p - ss_o
    ss_e = ss_t - ss_cells

    df_p, df_o, df_po, df_e = p - 1, o - 1, (p - 1) * (o - 1), n - p * o
    ms_p, ms_o, ms_po, ms_e = ss_p / df_p, ss_o / df_o, ss_po / df_po, ss_e / df_e
    f_po = ms_po / ms_e
    p_po = 1 - stats.f.cdf(f_po, df_po, df_e)

    keep = not (pool_alpha is not None and p_po > pool_alpha)
    if keep:
        f_p, f_o = ms_p / ms_po, ms_o / ms_po
        p_p, p_o = 1 - stats.f.cdf(f_p, df_p, df_po), 1 - stats.f.cdf(f_o, df_o, df_po)
        anova = pd.DataFrame({
            "Source": ["Part", "Operator", "Part×Operator", "Repeatability", "Total"],
            "DF": [df_p, df_o, df_po, df_e, n - 1],
            "SS": [ss_p, ss_o, ss_po, ss_e, ss_t],
            "MS": [ms_p, ms_o, ms_po, ms_e, np.nan],
            "F": [f_p, f_o, f_po, np.nan, np.nan],
            "P": [p_p, p_o, p_po, np.nan, np.nan]})
        v_rep = ms_e
        v_int = max(0.0, (ms_po - ms_e) / r)
        v_op = max(0.0, (ms_o - ms_po) / (p * r))
        v_part = max(0.0, (ms_p - ms_po) / (o * r))
    else:
        df_e2 = df_e + df_po
        ms_e2 = (ss_e + ss_po) / df_e2
        f_p, f_o = ms_p / ms_e2, ms_o / ms_e2
        anova = pd.DataFrame({
            "Source": ["Part", "Operator", "Repeatability", "Total"],
            "DF": [df_p, df_o, df_e2, n - 1],
            "SS": [ss_p, ss_o, ss_e + ss_po, ss_t],
            "MS": [ms_p, ms_o, ms_e2, np.nan],
            "F": [f_p, f_o, np.nan, np.nan],
            "P": [1 - stats.f.cdf(f_p, df_p, df_e2), 1 - stats.f.cdf(f_o, df_o, df_e2), np.nan, np.nan]})
        v_rep, v_int = ms_e2, 0.0
        v_op = max(0.0, (ms_o - ms_e2) / (p * r))
        v_part = max(0.0, (ms_p - ms_e2) / (o * r))

    v_repro = v_op + v_int
    v_grr = v_rep + v_repro
    v_tot = v_grr + v_part
    names = ["Total Gage R&R", "  Repeatability", "  Reproducibility", "    Operator",
             "    Part×Operator", "Part-to-Part", "Total Variation"]
    var = np.array([v_grr, v_rep, v_repro, v_op, v_int, v_part, v_tot])
    sd = np.sqrt(var)
    comp = pd.DataFrame({"Source": names, "VarComp": var, "%Contribution": 100 * var / v_tot,
                         "StdDev": sd, "StudyVar": k * sd, "%StudyVar": 100 * sd / sd[-1]})
    if tolerance:
        comp["%Tolerance"] = 100 * k * sd / tolerance
    grr_sv = float(comp["%StudyVar"].iloc[0])
    grr_tol = float(comp["%Tolerance"].iloc[0]) if tolerance else None
    ndc = int(np.floor(1.41 * np.sqrt(v_part) / np.sqrt(v_grr))) if v_grr > 0 else 99

    # which operator pulls reproducibility: mean offset vs the others, Welch t-test
    rows = []
    for op_, g in d.groupby(operator):
        others = d[d[operator] != op_][value]
        # compare on part-centred values so part mix does not matter
        centred = d[value] - d[part].map(pm)
        mine = centred[d[operator] == op_]
        rest = centred[d[operator] != op_]
        t, pv = stats.ttest_ind(mine, rest, equal_var=False)
        rows.append({"operator": op_, "offset": mine.mean() - rest.mean(), "p_value": pv,
                     "repeat_sd": d[d[operator] == op_].groupby(part)[value].std().mean()})
        _ = others
    bias = pd.DataFrame(rows).sort_values("offset", key=np.abs, ascending=False)

    return GageResult(anova, comp, grr_sv, grr_tol, ndc,
                      "reproducibility" if v_repro > v_rep else "repeatability", keep, bias, k)


def verdict(pct: float, ok: float = 10, marginal: float = 30) -> str:
    return "acceptable" if pct < ok else "marginal" if pct <= marginal else "unacceptable"
