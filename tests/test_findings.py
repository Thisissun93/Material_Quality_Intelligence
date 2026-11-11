"""End to end: the engine must find every story planted in the synthetic plant, and nothing absurd."""
from mqi import engine
from mqi.insight import drafts, rules


def test_viscosity_drift_traced_to_filler_d50_and_supplier(bundle):
    att = bundle.attribution["viscosity"]
    assert att.iloc[0]["feature"] == "d50" and att.iloc[0]["effect"] > 0
    share = engine.supplier_share(bundle, "viscosity")
    assert share.loc["NanoSphere", "alarm"] > share.loc["NanoSphere", "baseline"]


def test_early_warning_beats_three_sigma(bundle):
    f = bundle.spc["viscosity"]["first"]
    assert f["EWMA"] is not None and f["3σ (rule 1)"] is not None and f["EWMA"] < f["3σ (rule 1)"]


def test_untrusted_coa_is_the_drifting_supplier_only(bundle):
    bad = bundle.trust[bundle.trust["trust"] == "untrusted"]
    assert set(zip(bad["supplier"], bad["property"])) == {("NanoSphere", "d50")}


def test_instrument_bias_recovered_by_ancova(bundle):
    sh = engine.instrument_shift("tg", bundle.dbpath, b=bundle)
    assert sh["new"].startswith("CaloriX") and 1.4 < sh["diff"] < 3.0 and sh["p"] < 0.001


def test_hidden_combination_lot_is_found(bundle):
    t2 = bundle.t2
    lots = bundle.product.loc[t2[t2["alarm"] & t2["univariate_ok"]].index, "lot_id"].tolist()
    assert "UF-2609-012" in lots


def test_gage_flags_operator_park(bundle):
    assert bundle.gage.dominant == "reproducibility"
    assert bundle.gage.operator_bias.iloc[0]["operator"] == "Park"


def test_viscosity_model_is_useful(bundle):
    assert bundle.models["viscosity"].r2 > 0.8


def test_findings_and_drafts_render_in_both_languages(bundle):
    fs = rules.findings(bundle)
    assert fs and fs[0].severity == "high"
    for lang in ("ko", "en"):
        assert all(f.t("title", lang) and f.t("action", lang) for f in fs)
        assert "D4" in drafts.eight_d(bundle, "viscosity", lang)
        assert "NanoSphere" in drafts.scar(bundle, "NanoSphere", "d50", lang)
