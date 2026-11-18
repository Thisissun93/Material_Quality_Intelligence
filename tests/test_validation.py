"""Every statistic in docs/stats_validation.md must match its independent computation."""
from mqi import validation


def test_all_statistics_match_independent_computation(bundle):
    df = validation.run(bundle)
    bad = df[~df["ok"]]
    assert bad.empty, bad[["area", "item", "app", "reference", "diff"]].to_string()
    assert len(df) >= 25
    assert {"Gage R&R", "공정능력", "SPC", "다변량"} <= set(df["area"])
