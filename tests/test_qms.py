"""Action register, complaints, process / 4M change screens, outgoing CoA, supplier uncertainty."""
from datetime import date, timedelta

from mqi import outgoing, qms
from mqi.analytics import changes
from mqi.insight import eightd


def test_small_supplier_is_pending_not_tightened(bundle):
    s = bundle.score.set_index("supplier")
    assert s.loc["KoPolymer", "inspection"] == "pending"
    assert s.loc["NanoSphere", "inspection"] == "tightened"


def test_process_record_clears_machine_and_method(bundle):
    ps = changes.process_screen(bundle, "viscosity", 60)
    assert set(ps["param"]) >= {"mixer", "vacuum", "mix_time"}
    assert not ps["linked"].any()
    r = eightd.build(bundle, "viscosity", "en")
    machine = next(m for m in r.d4["m6"] if m[0] == "Machine")
    assert machine[3] == "Excluded"


def test_change_log_points_at_dsc_and_flags_unnotified_supplier_change(bundle):
    tg = eightd.build(bundle, "tg", "en")
    verdict = {c[0]: c[5] for c in tg.changes}
    assert verdict["CN-26-052"] == "Candidate"
    visc = eightd.build(bundle, "viscosity", "en")
    assert all(c[5] == "Excluded" for c in visc.changes)
    assert "no notification" in visc.d4["occ_mrc"]


def test_complaint_8d_and_action_register(bundle):
    cc = qms.complaints(bundle.dbpath)
    assert len(cc) == 1
    c = qms.complaint(cc["no"][0], bundle.dbpath)
    r = eightd.build(bundle, c["property"], "en", c)
    assert r.no.startswith("8D-CC-") and "Customer stock" in r.d3["rows"][0][0]
    rows = eightd.register_rows(r)
    assert qms.register(r.no, rows, bundle.dbpath) == len(rows)
    assert qms.register(r.no, rows, bundle.dbpath) == 0          # idempotent
    a = qms.actions(r.no, bundle.dbpath)
    a.loc[a["step"] == "D5", "status"] = "Done"
    a.loc[a.index[0], "due"] = (date.today() - timedelta(days=3)).isoformat()
    qms.update(a, bundle.dbpath)
    r2 = eightd.build(bundle, c["property"], "en", c)
    assert all(row[4] == "Done" for row in r2.d6["rows"])
    assert qms.summary(bundle.dbpath)["overdue"] == 1


def test_release_check_and_outgoing_coa(bundle):
    st = outgoing.batch_status(bundle, 120).set_index("lot_id")
    assert st.loc["UF-2609-012", "status"] == "hold"                 # hidden T² lot
    lot = qms.complaints(bundle.dbpath)["lot_id"][0]
    assert st.loc[lot, "status"] == "hold"                            # open complaint
    pdf = outgoing.pdf(bundle, lot, "Customer A (demo)", draft=True)
    assert pdf[:4] == b"%PDF" and len(pdf) > 2000
