"""Statistics checked against independent implementations and planted ground truth."""
import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from statsmodels.formula.api import ols

from mqi.analytics import capability, msa, spc


def _gage(seed=1, bias=(0, 0, 60), rep_sd=30):
    rng = np.random.default_rng(seed)
    rows = []
    for p, true in enumerate(np.linspace(2900, 4100, 10)):
        for o, b in zip("ABC", bias):
            for r in range(3):
                rows.append((str(p), o, r, true + b + rng.normal(0, rep_sd)))
    return pd.DataFrame(rows, columns=["part", "operator", "replicate", "value"])


def test_gage_anova_matches_statsmodels():
    d = _gage()
    g = msa.gage_rr(d)
    ref = sm.stats.anova_lm(ols("value ~ C(part) * C(operator)", d).fit(), typ=2)
    ss = dict(zip(g.anova["Source"], g.anova["SS"]))
    assert ss["Part"] == pytest.approx(ref.loc["C(part)", "sum_sq"], rel=1e-9)
    assert ss["Operator"] == pytest.approx(ref.loc["C(operator)", "sum_sq"], rel=1e-9)
    assert ss["Part×Operator"] == pytest.approx(ref.loc["C(part):C(operator)", "sum_sq"], rel=1e-9)
    assert ss["Repeatability"] == pytest.approx(ref.loc["Residual", "sum_sq"], rel=1e-9)


def test_gage_finds_the_biased_operator():
    g = msa.gage_rr(_gage(bias=(0, 0, 80)))
    assert g.dominant == "reproducibility"
    assert g.operator_bias.iloc[0]["operator"] == "C"


def test_gage_variance_components_recover_truth():
    g = msa.gage_rr(_gage(seed=3, bias=(0, 0, 0), rep_sd=25))
    rep_sd = g.components.set_index("Source").loc["  Repeatability", "StdDev"]
    assert rep_sd == pytest.approx(25, rel=0.2)


def test_capability_formulas():
    rng = np.random.default_rng(0)
    x = rng.normal(100, 2, 5000)
    c = capability.analyze(x, 94, 106)
    assert c.ppk == pytest.approx(1.0, abs=0.05)
    assert c.sd_within == pytest.approx(2, rel=0.05)      # MR-bar / d2 on iid data
    assert c.ppm == pytest.approx(2700, rel=0.25)          # ±3σ two-sided


def test_cpk_much_larger_than_ppk_when_mean_drifts():
    x = np.linspace(0, 6, 200) + np.random.default_rng(1).normal(0, 0.5, 200)
    c = capability.analyze(x, -6, 12)
    assert c.cpk - c.ppk > 0.5


def test_ewma_detects_small_shift_earlier_than_3sigma():
    """Average over many simulated runs: a 1σ step after lot 40."""
    leads = []
    for seed in range(60):
        rng = np.random.default_rng(seed)
        x = np.r_[rng.normal(0, 1, 40), rng.normal(1.0, 1, 80)]
        s = spc.detection_summary(x, 40, {"ewma_lambda": 0.2, "ewma_L": 2.7, "sigma": "overall"})
        three = s["first"]["3σ (rule 1)"] or 120
        ew = s["first"]["EWMA"] or 120
        leads.append(three - ew)
    assert np.mean(leads) > 5


def test_nelson_rule_2_run_of_nine():
    lim = spc.Limits(0, 1)
    x = np.r_[np.zeros(5) - 0.2, np.full(9, 0.5)]
    assert spc.nelson(x, lim)[2].iloc[-1]


def test_t2_flags_abnormal_combination_with_normal_singles():
    rng = np.random.default_rng(4)
    cov = [[1, 0.9], [0.9, 1]]
    X = rng.multivariate_normal([0, 0], cov, 80)
    X[-1] = [1.8, -1.8]                       # each |z| < 3, but against the correlation
    h = spc.hotelling(pd.DataFrame(X, columns=["a", "b"]), 40, window=None)
    assert h["alarm"].iloc[-1] and h["univariate_ok"].iloc[-1]
