"""Every statistic in docs/stats_validation.md must match its independent computation."""
from mqi import validation


def test_all_statistics_match_independent_computation(bundle):
    df = validation.run(bundle)
    bad = df[~df["ok"]]
    assert bad.empty, bad[["area", "item", "app", "reference", "diff"]].to_string()
    assert len(df) >= 25
    assert {"Gage R&R", "공정능력", "SPC", "다변량"} <= set(df["area"])


def test_t2_check_follows_fixed_baseline_mode(bundle, monkeypatch):
    """Codex review: with t2_window null the validator must check the fixed-baseline T² the app produced."""
    from mqi import config
    from mqi.analytics import spc
    from mqi.engine import T2_PROPS
    import copy
    rules = copy.deepcopy(config.rules())                     # never mutate the cached config
    rules["spc"]["t2_window"] = None
    monkeypatch.setattr(config, "rules", lambda: rules)
    cols = [p for p in T2_PROPS if p in bundle.product]
    fixed = spc.hotelling(bundle.product[cols], bundle.baseline, rules["spc"]["t2_alpha"], None)
    monkeypatch.setattr(bundle, "t2", fixed)
    rows = validation._t2(bundle)
    assert all(r["ok"] for r in rows)
    assert "고정 기준" in rows[0]["item"]


def test_spc_check_follows_moving_range_sigma(bundle, monkeypatch):
    """Codex review: with spc.sigma = moving_range the reference must use MR-bar/d2 too."""
    import copy
    from mqi import config
    rules = copy.deepcopy(config.rules())
    rules["spc"]["sigma"] = "moving_range"
    monkeypatch.setattr(config, "rules", lambda: rules)
    rows = validation._spc(bundle)
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
