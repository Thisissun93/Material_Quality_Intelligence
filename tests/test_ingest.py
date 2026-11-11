"""Instrument matching, unit handling, CoA reading and format learning."""
import numpy as np
import pytest

from mqi import units
from mqi.ingest import coa
from mqi.instruments import registry

EXPECTED = {  # file prefix -> profile that must win
    "DV2_resin": "rotavisc_dv2_fw3", "DV2-legacy": "rotavisc_dv2_fw2", "DV2_": "rotavisc_dv2_fw3",
    "MCR_": "rheopro_mcr", "DSC_": "thermatech_dsc", "CaloriX_": "calorix_dsc", "TGA_": "thermoscale_tga",
    "PSD_": "lasersizer_psd", "KF_": "kf_titrator", "Titration_": "autotitrator", "ERP_": "erp_lot_master",
    "MES_": "mes_genealogy", "GageRR": "gage_study", "CoA_": "coa_reader",
}


def _expected(name):
    for k in sorted(EXPECTED, key=len, reverse=True):
        if name.startswith(k):
            return EXPECTED[k]
    return None


def test_every_known_export_is_matched_to_the_right_instrument(demo):
    res = demo["results"]
    wrong = [(r.file, r.profile) for r in res if _expected(r.file) and r.profile != _expected(r.file)]
    assert not wrong, wrong
    assert all(r.status == "stored" for r in res if _expected(r.file))


def test_unknown_instrument_is_not_forced_into_a_wrong_profile(demo):
    r = next(r for r in demo["results"] if r.file.startswith("ViscoLab"))
    assert r.status == "unknown"


def test_firmware_versions_are_told_apart(demo):
    fw2 = next(demo["folder"].glob("DV2-legacy_*.csv"))
    _, m = registry.rank(fw2)
    assert m[0].id == "rotavisc_dv2_fw2"
    assert m[0].confidence - m[1].confidence > 0.1


def test_renamed_header_still_matches(demo, tmp_path):
    src = next(p for p in demo["folder"].glob("DV2_2026-*.csv"))
    alt = tmp_path / "dv2_renamed.csv"
    alt.write_text(src.read_text().replace("Viscosity (cP)", "Viscosity [cP]"))
    status, top = registry.decide(registry.rank(alt)[1])
    assert top.id == "rotavisc_dv2_fw3" and status == "auto"


def test_learn_new_format_once(demo, tmp_path):
    f = demo["folder"] / "ViscoLab_retest.txt"
    fp, _ = registry.rank(f)
    mapping = registry.propose_mapping(fp)
    got = {m["field"]: m["column"] for m in mapping if m["field"]}
    assert got == {"lot_id": "Sample_No", "viscosity": "Visco_mPas", "operator": "Tested by",
                   "measured_at": "Test date"}
    registry.save_profile(fp, mapping, "ViscoLab VL-9", "viscometer", directory=tmp_path)
    profiles = registry.load_profiles() + registry.load_profiles(tmp_path)
    status, top = registry.decide(registry.rank(f, profiles)[1])
    assert status == "auto" and top.id == "viscolab_vl_9"


@pytest.mark.parametrize("v,u,canon,exp", [
    (3.5, "Pa·s", "mPa·s", 3500), (3500, "cP", "mPa·s", 3500), (420.15, "K", "°C", 147.0),
    (400, "ppm", "%", 0.04), (2500, "nm", "µm", 2.5)])
def test_unit_conversion(v, u, canon, exp):
    assert units.convert([v], u, canon)[0] == pytest.approx(exp)


def test_unit_inferred_from_magnitude_when_header_has_none():
    u, score = units.infer_unit([3.4, 3.6, 3.9], "mPa·s", (100, 100000))
    assert units._key(u) in ("pa·s", "pas", "pa.s") and score == 1


def test_header_unit_parser_ignores_non_units():
    assert units.unit_from_header("Enthalpy (normalized)") is None
    assert units.unit_from_header("Peak/K") == "K"
    assert units.unit_from_header("Visc[mPa.s]") == "mPa.s"


def test_coa_reader_handles_two_supplier_layouts(demo):
    t, _ = coa.extract(next(demo["folder"].glob("CoA_SF-SI-*.pdf")))   # table layout
    d, _ = coa.extract(next(demo["folder"].glob("CoA_SF-NA-*.pdf")))   # dotted layout, ppm
    for df in (t, d):
        v = df.set_index("property")["value"]
        assert 2.0 < v["d50"] < 4.0 and 0 < v["moisture"] < 0.2
