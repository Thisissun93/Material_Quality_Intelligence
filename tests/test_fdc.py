"""Equipment data: FDC rules, the demo plant, the collector (batching, store-and-forward) and OPC UA."""
import asyncio
import socket

import pandas as pd
import pytest

from mqi import engine, fdc, outgoing
from mqi.demo import FDC_DEMO_LOT
from mqi.equipment_model import Fault, Recipe, batch_trace


# ---------------------------------------------------------------- rules

def _judge(trace):
    tr = fdc.resample(trace)
    return fdc.check(fdc.summarize(tr), tr)


def test_normal_batches_raise_no_alarm():
    """No false alarms over 30 normal batches with recipe variation like the MES record."""
    for i in range(30):
        rec = Recipe(mix_min=42 + i % 7, degas_temp=59 + (i % 5) * 0.4, vacuum=4.5 + (i % 3) * 0.5)
        assert _judge(batch_trace(f"L{i}", "M1" if i % 2 else "M2", rec)) == [], i


def test_tc1_drift_is_caught_by_the_second_sensor():
    alarms = _judge(batch_trace("L1", "M1", Recipe(), Fault("tc1_drift", 5.0)))
    assert alarms[0]["rule"] == "dual_sensor"                       # cause first
    assert any(a["rule"] == "limit_max" and a["tag"] == "product_temp_c" for a in alarms)
    # TC1 alone looks perfect: the controller keeps it on set point
    tr = batch_trace("L1", "M1", Recipe(), Fault("tc1_drift", 5.0))
    tc1 = tr[(tr.step == "DEGAS") & (tr.tag == "jacket_tc1_c")]["value"]
    assert tc1.max() < 61


def test_short_mix_is_a_duration_alarm():
    alarms = _judge(batch_trace("L2", "M1", Recipe(mix_min=30)))
    assert [a["rule"] for a in alarms] == ["duration"]


def test_resample_handles_irregular_polling():
    tr = batch_trace("L3", "M1", Recipe(), dt=7)          # collector polled every 7 s
    r = fdc.resample(tr)
    assert r["t_s"].diff().replace(0, pd.NA).dropna().min() >= 30
    assert _judge(tr) == []


# ---------------------------------------------------------------- demo plant and release

def test_demo_flags_only_the_planted_run(bundle):
    runs = fdc.runs(dbpath=bundle.dbpath)
    assert len(runs) == 20
    assert runs[runs.status == "alarm"]["lot_id"].tolist() == [FDC_DEMO_LOT]
    lvl, why = outgoing.release_check(bundle, FDC_DEMO_LOT)
    assert lvl == "hold" and "control sensor" in why[-1][1]


def test_overview_lists_the_equipment_alarm(bundle):
    from mqi.insight import rules
    assert any(f.key == f"fdc_{FDC_DEMO_LOT}" for f in rules.findings(bundle))


def test_incomplete_run_is_not_judged(demo, tmp_path):
    from conftest import clone_store
    store = clone_store(demo["db"], tmp_path)
    tr = batch_trace("UF-2611-006", "M1", Recipe(), Fault())
    tr = tr[tr.t_s > 3000]                                  # collector joined during DEGAS
    r = fdc.record_run("UF-2611-006", "M1", tr, complete=False, dbpath=store)
    assert r["status"] == "incomplete" and r["alarms"] == []
    assert fdc.flags("UF-2611-006", store) == []
    assert outgoing.release_check(engine.run(store), "UF-2611-006")[0] == "release"


# ---------------------------------------------------------------- collector

def _snapshots(lot, eq="M1", fault=None, start_at=0.0, dt=30.0):
    from mqi.equipment_model import Mixer
    m = Mixer(lot, eq, Recipe(), fault)
    while not m.done:
        if m.t >= start_at:
            yield lot, m.step, m.t, m.values()
        m.advance(dt)
    yield "", "IDLE", m.t, {}


def test_batch_assembler_cuts_batches():
    from collector.main import BatchAssembler
    a, out = BatchAssembler("M1"), []
    for snap in list(_snapshots("A")) + list(_snapshots("B")):
        p = a.feed(*snap)
        if p:
            out.append(p)
    assert [p["lot_id"] for p in out] == ["A", "B"]
    assert all(p["complete"] for p in out)
    assert {s["step"] for s in out[0]["samples"]} == {"CHARGE", "MIX", "DEGAS", "DISCHARGE"}


def test_batch_assembler_marks_late_join_incomplete():
    from collector.main import BatchAssembler
    a = BatchAssembler("M1")
    done = [p for s in _snapshots("A", start_at=1800) if (p := a.feed(*s))]
    assert len(done) == 1 and done[0]["complete"] is False


def test_store_and_forward_keeps_order(tmp_path):
    from collector.main import Sender
    up, got = {"ok": False}, []

    async def post(p):
        if not up["ok"]:
            raise ConnectionError("api down")
        got.append(p["lot_id"])

    s = Sender(post, tmp_path)

    async def scenario():
        await s.deliver({"lot_id": "A", "equipment": "M1", "samples": []})
        await s.deliver({"lot_id": "B", "equipment": "M1", "samples": []})
        assert s.pending() == 2 and got == []
        up["ok"] = True
        await s.deliver({"lot_id": "C", "equipment": "M1", "samples": []})

    asyncio.run(scenario())
    assert got == ["A", "B", "C"] and s.pending() == 0


# ---------------------------------------------------------------- API

def test_api_receives_and_judges_a_run(client):
    tr = batch_trace("UF-2611-006", "M2", Recipe(), Fault())
    body = {"lot_id": "UF-2611-006", "equipment": "M2", "source": "opcua", "samples": tr.to_dict("records")}
    from conftest import login

    assert client.post("/api/v1/equipment/runs", json=body).status_code == 403     # QA is not the collector
    client.headers.update(login(client, "collector"))
    r = client.post("/api/v1/equipment/runs", json=body)
    assert r.status_code == 201 and r.json()["status"] == "alarm"
    rid = r.json()["id"]
    d = client.get(f"/api/v1/equipment/runs/{rid}", params={"trace": True}).json()
    assert d["alarms"][0]["rule"] == "dual_sensor" and len(d["trace"]) > 1000
    assert client.get("/api/v1/lots/UF-2611-006/release").json()["status"] == "hold"
    assert client.post("/api/v1/equipment/runs", json={**body, "samples": []}).status_code == 422


# ---------------------------------------------------------------- OPC UA end to end

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_opcua_simulator_to_collector():
    """Real OPC UA: simulator publishes one accelerated batch, the collector reads and cuts it."""
    pytest.importorskip("asyncua")
    from collector.main import poll_opcua
    from equipment.opcua_sim import build_server, run_mixer

    url = f"opc.tcp://127.0.0.1:{_free_port()}/mqi/"
    got = []

    async def scenario():
        server, nodes = await build_server(url, ["M1"])
        stop = asyncio.Event()

        async def on_batch(p):
            got.append(p)
            stop.set()

        async with server:
            col = asyncio.create_task(poll_opcua(url, ["M1"], on_batch, poll_s=0.02, stop=stop))
            await asyncio.sleep(1.0)                    # let the collector connect before the batch starts
            sim = asyncio.create_task(run_mixer(nodes["M1"], iter(["UF-TEST-1"]), {"UF-TEST-1"}, accel=1500,
                                                tick=0.02, batches=1))
            await asyncio.wait_for(stop.wait(), timeout=60)
            sim.cancel()
            await col

    asyncio.run(scenario())
    p = got[0]
    assert p["lot_id"] == "UF-TEST-1" and p["complete"]
    tr = pd.DataFrame(p["samples"])
    assert set(tr["step"]) == {"CHARGE", "MIX", "DEGAS", "DISCHARGE"}
    assert _judge(tr)[0]["rule"] == "dual_sensor"      # the drift injected in the simulator is found
