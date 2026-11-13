"""OOS handling: the first failing result is kept, a passing retest cannot release the lot on its own,
and only a proven laboratory error sets the failing result aside."""
import shutil

import pytest

from mqi import db, engine, oos, outgoing


@pytest.fixture()
def plant(demo, tmp_path):
    """A private copy of the demo database, so these tests can write to it."""
    path = tmp_path / "mqi.sqlite"
    shutil.copy(demo["db"], path)
    return path


def _fresh_lot(path):
    b = engine.run(path)
    st = outgoing.batch_status(b, 40)
    return b, st[st["status"] == "release"]["lot_id"].iloc[-1]


def _fail_first(path, lot, prop="viscosity", value=4620):
    """Make the first in-house result of a lot fail (as if the instrument had reported it)."""
    with db.connect(path) as con:
        con.execute("UPDATE measurements SET value=? WHERE lot_id=? AND property=? AND source='inhouse'",
                    (value, lot, prop))


def test_demo_plants_an_oos_retested_without_investigation(bundle):
    h = db.product_history(prop="viscosity", path=bundle.dbpath)
    twice = h.groupby("lot_id").size().loc[lambda s: s > 1].index
    assert len(twice) == 1
    lot = twice[0]
    hist = oos.history(lot, "viscosity", bundle.dbpath)
    assert list(hist["attempt"]) == ["initial", "remeasure"]
    assert hist["oos"].tolist() == [True, False]
    lvl, why = outgoing.release_check(bundle, lot)
    assert lvl == "block" and any("OOS investigation required" in w[1] for w in why)


def test_passing_retest_does_not_release_and_first_result_is_kept(plant):
    b, lot = _fresh_lot(plant)
    _fail_first(plant, lot)
    with pytest.raises(ValueError):                        # no retest before an investigation exists
        oos.add_retest(lot, "viscosity", 3480, "remeasure", "check", "Kim", dbpath=plant)
    no = oos.open_investigation(lot, "viscosity", "QC lead Lee", plant)
    oos.add_retest(lot, "viscosity", 3480, "remeasure", "Same retained sample, spindle re-zeroed", "Kim",
                   dbpath=plant)

    b = engine.run(plant)
    assert b.product.set_index("lot_id").loc[lot, "viscosity"] == 3480        # reportable = retest
    h = oos.history(lot, "viscosity", plant)
    assert h["value"].tolist() == [4620, 3480]                                # original still on record
    lvl, why = outgoing.release_check(b, lot)
    assert lvl == "block" and any(no in w[1] and "phase 1" in w[1] for w in why)


def test_only_a_proven_lab_error_invalidates_the_first_result(plant):
    b, lot = _fresh_lot(plant)
    _fail_first(plant, lot)
    no = oos.open_investigation(lot, "viscosity", "Lee", plant)
    oos.add_retest(lot, "viscosity", 3480, "remeasure", "Same sample", "Kim", dbpath=plant)
    with pytest.raises(ValueError):                        # lab error needs a category
        oos.record_phase1(no, True, "spindle not zeroed", "Lee", dbpath=plant)
    oos.record_phase1(no, True, "Torque zero check failed (log 11-13); passed after re-zero", "Lee",
                      "instrument", plant)
    b = engine.run(plant)
    lvl, why = outgoing.release_check(b, lot)
    assert lvl == "release" and any("invalidated" in w[1] for w in why)
    h = oos.history(lot, "viscosity", plant)
    assert h["invalidated"].tolist() == [True, False] and h["value"].tolist() == [4620, 3480]


def test_no_lab_error_means_the_failure_stands(plant):
    b, lot = _fresh_lot(plant)
    _fail_first(plant, lot)
    no = oos.open_investigation(lot, "viscosity", "Lee", plant)
    oos.add_retest(lot, "viscosity", 3480, "resample", "Second drum", "Kim", dbpath=plant)
    with pytest.raises(ValueError):                        # phase 2 only after phase 1
        oos.record_phase2(no, "x", "scrap", "Park", plant)
    oos.record_phase1(no, False, "Instrument, standard and calculation checked: no error", "Lee", dbpath=plant)
    b = engine.run(plant)
    assert outgoing.release_check(b, lot)[0] == "block"      # retest passed, still blocked
    oos.record_phase2(no, "Filler dispersion uneven between drums", "rework", "Production Park", plant)
    lvl, why = outgoing.release_check(engine.run(plant), lot)
    assert lvl == "block" and any("OOS confirmed" in w[1] for w in why)


def test_repeat_without_reason_is_held(plant):
    b, lot = _fresh_lot(plant)
    with db.connect(plant) as con:                         # a second instrument export of the same lot
        con.execute("INSERT INTO measurements (lot_id, material, property, value, unit, source, operator, "
                    "measured_at, source_file) VALUES (?,?,?,?,?,?,?,?,?)",
                    (lot, "product", "viscosity", 3500, "mPa·s", "inhouse", "Kim", "2099-01-01", "re-export.csv"))
    assert outgoing.release_check(engine.run(plant), lot)[0] == "hold"


def test_overview_lists_the_blocked_lot(bundle):
    from mqi.insight import rules
    assert any(f.key.startswith("oos_") and f.severity == "high" for f in rules.findings(bundle))
