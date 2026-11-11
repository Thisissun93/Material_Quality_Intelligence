from io import BytesIO

from openpyxl import load_workbook

from mqi.ingest import coa_docs
from mqi.insight import eightd


def test_8d_has_every_discipline_and_both_root_causes(bundle):
    for lang in ("ko", "en"):
        r = eightd.build(bundle, "viscosity", lang)
        md = eightd.markdown(r)
        for d in [f"## D{i}" for i in range(9)]:
            assert d in md
        assert "IS NOT" in md and "5W2H" in md and "TRC" in md and "MRC" in md
        assert "NanoSphere" in r.d4["occ_trc"] and "NanoSphere" in r.d4["nd_trc"]
        assert len(r.d3["rows"]) >= 3 and all(row[3] and row[4] for row in r.d3["rows"])


def test_8d_switches_to_measurement_cause_for_dsc(bundle):
    r = eightd.build(bundle, "tg", "en")
    assert "CaloriX" in r.d4["occ_trc"]
    assert any(m[0] == "Material" and m[3] == "Excluded" for m in r.d4["m6"])


def test_8d_raw_limit_is_tighter_than_current_spec(bundle):
    lim = eightd._limit_for(bundle, "viscosity", "d50")
    assert lim is not None and 2.4 < lim < 3.0


def test_8d_excel_opens(bundle):
    wb = load_workbook(BytesIO(eightd.xlsx(eightd.build(bundle, "viscosity", "ko"))))
    assert wb.active.max_row > 40


def test_coa_documents_flag_nanosphere(bundle):
    docs = coa_docs.documents(bundle.dbpath)
    worst = docs.sort_values("gap_pct", ascending=False).head(3)
    assert set(worst["supplier"]) == {"NanoSphere"}
    c = coa_docs.compare(worst.iloc[0]["file"], bundle.dbpath)
    assert (c.loc[c["property"] == "d50", "verdict"] == "gap").all()
