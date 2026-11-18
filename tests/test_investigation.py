"""Investigation pack: one call gathers what an engineer collects across systems for a lot."""
from mqi import demo, investigation, qms


def test_complaint_pack_has_every_section(bundle):
    lot = qms.complaints(bundle.dbpath).iloc[0]["lot_id"]
    p = investigation.collect(bundle, lot)
    assert p.prop == "viscosity"
    assert p.level == "hold"                                   # open complaint holds the lot
    assert set(p.data) == {k for k, _, _ in investigation.SECTIONS}
    assert len(p.data["genealogy"]) == 3                       # resin, hardener, filler
    assert set(p.data["raw"]["lot_id"]) == set(p.data["genealogy"]["raw_lot"])
    assert set(p.data["coa_trust"]["supplier"]) <= set(p.data["genealogy"]["supplier"])
    assert len(p.data["complaints"]) == 1
    assert lot not in set(p.data["same_raw"]["product_lot"])   # containment list excludes the lot itself
    assert "D4" in p.data["eightd"]
    assert {"LIMS", "MES", "QMS"} <= set(p.systems)
    assert p.n_records > 30 and p.total_seconds < 10


def test_oos_pack_keeps_first_result_and_blocks(bundle):
    p = investigation.collect(bundle, demo.OOS_DEMO_LOT, "viscosity")
    assert p.level == "block"
    h = p.data["history"]
    v = h[h["property"] == "viscosity"]
    assert (v["attempt"] == "initial").any() and v["oos"].any()


def test_fdc_pack_shows_the_alarm(bundle):
    p = investigation.collect(bundle, demo.FDC_DEMO_LOT)
    eq = p.data["equipment"]
    assert (eq["status"] == "alarm").any()
    assert eq["alarms"].str.contains("TC").any()


def test_markdown_lists_sections(bundle):
    p = investigation.collect(bundle, demo.FDC_DEMO_LOT)
    md = investigation.markdown(p)
    assert md.startswith(f"# 조사 패키지: {demo.FDC_DEMO_LOT}")
    assert "투입 원료 로트" in md and "같은 원료를 쓴 다른 제품 로트" in md
