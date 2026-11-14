"""FDC: summarise each recipe step of an equipment run and judge it against config/fdc.yaml.

The collector (or the demo) hands over a raw trace (t_s, step, tag, value). Everything else happens
here, on the server, so limits live in one place and every mixer is judged the same way:

  1. resample to a fixed grid (collectors poll at different rates)
  2. step summaries: n, mean, min, max, final (last 20 %), slope, duration
  3. rules: step duration window, limits on a summary statistic, and dual-sensor agreement
  4. store run, summaries, alarms and the trace; an alarm puts the lot on QA hold
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import config, db


def _cfg() -> dict:
    return config.fdc()


def tag_label(tag: str, lang: str = "ko") -> str:
    t = _cfg()["tags"].get(tag)
    return (t[0] if lang == "ko" else t[1]) if t else tag


def step_label(step: str, lang: str = "ko") -> str:
    s = _cfg()["step_names"].get(step)
    return (s[0] if lang == "ko" else s[1]) if s else step


def _n(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.1f}"


def _unit(tag: str) -> str:
    t = _cfg()["tags"].get(tag)
    return t[2] if t else ""


# ---------------------------------------------------------------- 1. resample

def resample(trace: pd.DataFrame, period: float | None = None) -> pd.DataFrame:
    """Long trace on a fixed time grid: mean value per tag per bin, step = most frequent step in the bin."""
    if trace.empty:
        return trace
    period = period or float(_cfg()["sample_period_s"])
    t = trace.copy()
    t["t_s"] = (t["t_s"] // period) * period
    val = t.groupby(["t_s", "tag"], as_index=False)["value"].mean()
    stp = t.groupby("t_s")["step"].agg(lambda s: s.value_counts().index[0])
    val["step"] = val["t_s"].map(stp)
    return val[["t_s", "step", "tag", "value"]].sort_values(["t_s", "tag"]).reset_index(drop=True)


# ---------------------------------------------------------------- 2. summaries

def summarize(trace: pd.DataFrame) -> pd.DataFrame:
    """One row per step and tag."""
    rows = []
    period = float(_cfg()["sample_period_s"])
    for (step, tag), g in trace.groupby(["step", "tag"], sort=False):
        g = g.sort_values("t_s")
        v, ts = g["value"].to_numpy(float), g["t_s"].to_numpy(float)
        k = max(1, int(np.ceil(len(v) * 0.2)))
        slope = float(np.polyfit(ts / 60, v, 1)[0]) if len(v) >= 3 and np.ptp(ts) > 0 else 0.0
        dur = (ts.max() - ts.min() + period) / 60
        rows.append(dict(step=step, tag=tag, n=len(v), mean=v.mean(), min=v.min(), max=v.max(),
                         final=v[-k:].mean(), slope_per_min=slope, duration_min=dur))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 3. rules

def check(summary: pd.DataFrame, trace: pd.DataFrame) -> list[dict]:
    c, out = _cfg(), []
    if summary.empty:
        return [dict(step="", rule="no_data", tag="", value=None, lim=None,
                     message_ko="설비 데이터 없음", message_en="No equipment data")]
    s = summary.set_index(["step", "tag"])
    steps = set(summary["step"])

    for step, (lo, hi) in (c.get("duration_min") or {}).items():
        if step not in steps:
            out.append(dict(step=step, rule="missing_step", tag="", value=None, lim=None,
                            message_ko=f"{step_label(step)} 단계 데이터 없음",
                            message_en=f"No data for the {step_label(step, 'en')} step"))
            continue
        d = float(summary.loc[summary["step"] == step, "duration_min"].max())
        if not lo <= d <= hi:
            lim = lo if d < lo else hi
            out.append(dict(step=step, rule="duration", tag="", value=d, lim=lim,
                            message_ko=f"{step_label(step)} 시간 {d:.0f}분 (기준 {lo}~{hi}분)",
                            message_en=f"{step_label(step, 'en')} took {d:.0f} min (window {lo}-{hi} min)"))

    for step, tags in (c.get("limits") or {}).items():
        for tag, lim in tags.items():
            if (step, tag) not in s.index:
                continue
            stat = lim.get("stat", "mean")
            v = float(s.loc[(step, tag), stat])
            u = _unit(tag)
            for side, bound in (("lo", lim.get("lo")), ("hi", lim.get("hi"))):
                if bound is None or (side == "lo" and v >= bound) or (side == "hi" and v <= bound):
                    continue
                word_ko, word_en = ("하한", "below") if side == "lo" else ("상한", "above")
                out.append(dict(step=step, rule=f"limit_{stat}", tag=tag, value=v, lim=bound,
                                message_ko=f"{step_label(step)} {tag_label(tag)} {stat} {_n(v)}{u} "
                                           f"({word_ko} {_n(bound)}{u} 이탈)",
                                message_en=f"{step_label(step, 'en')} {tag_label(tag, 'en')} {stat} {_n(v)}{u} "
                                           f"{word_en} limit {_n(bound)}{u}"))

    if not trace.empty:
        w = trace.pivot_table(index=["t_s", "step"], columns="tag", values="value").reset_index()
        for d in c.get("dual_sensor") or []:
            a, b = d["a"], d["b"]
            if a not in w or b not in w:
                continue
            seg = w[w["step"].isin(d.get("steps", w["step"].unique()))]
            if seg.empty:
                continue
            diff = (seg[a] - seg[b]).abs()
            v = float(diff.max())
            if v > d["max_diff"]:
                where = seg.loc[diff.idxmax(), "step"]
                out.append(dict(step=where, rule="dual_sensor", tag=f"{a}|{b}", value=v, lim=d["max_diff"],
                                message_ko=f"{step_label(where)} {tag_label(a)} vs {tag_label(b)} 편차 {v:.1f}"
                                           f"{_unit(a)} (허용 {d['max_diff']}{_unit(a)}) — 제어 센서 이상 의심",
                                message_en=f"{step_label(where, 'en')} {tag_label(a, 'en')} vs {tag_label(b, 'en')} "
                                           f"differ by {v:.1f}{_unit(a)} (max {d['max_diff']}) — suspect the "
                                           f"control sensor"))
    # the sensor disagreement explains the limit breaches that follow from it: report it first
    order = {"dual_sensor": 0, "missing_step": 1, "duration": 2}
    return sorted(out, key=lambda a: order.get(a["rule"], 3))


# ---------------------------------------------------------------- 4. store

def record_run(lot_id: str, equipment: str, trace: pd.DataFrame, started: str | None = None,
               ended: str | None = None, source: str = "opcua", complete: bool = True, dbpath=None) -> dict:
    """Summarise, judge and store one run. Returns {id, status, alarms}.

    A run the collector joined half-way (complete=False) is stored and summarised but not judged:
    missing steps would be a collection gap, not an equipment fault, and must not put a lot on hold.
    """
    if not lot_id or not equipment:
        raise ValueError("lot_id and equipment are required")
    tr = resample(trace[["t_s", "step", "tag", "value"]].dropna())
    summ = summarize(tr)
    alarms = check(summ, tr) if complete else []
    status = ("alarm" if alarms else "ok") if complete else "incomplete"
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect(dbpath) as con:
        rid = con.execute("INSERT INTO equipment_runs (lot_id, equipment, started, ended, source, received, samples, "
                          "status) VALUES (?,?,?,?,?,?,?,?)",
                          (lot_id, equipment, started or now, ended or now, source, now,
                           int(tr["t_s"].nunique()) if len(tr) else 0, status)).lastrowid
        if len(summ):
            con.executemany("INSERT INTO fdc_summary (run_id, step, tag, n, mean, min, max, final, slope_per_min, "
                            "duration_min) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            [(rid, r.step, r.tag, int(r.n), float(r.mean), float(r.min), float(r.max),
                              float(r.final), float(r.slope_per_min), float(r.duration_min))
                             for r in summ.itertuples()])
        if alarms:
            con.executemany("INSERT INTO fdc_alarms (run_id, step, rule, tag, value, lim, message_ko, message_en) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            [(rid, a["step"], a["rule"], a["tag"], a["value"], a["lim"], a["message_ko"],
                              a["message_en"]) for a in alarms])
        if len(tr):
            con.executemany("INSERT INTO equipment_trace (run_id, t_s, step, tag, value) VALUES (?,?,?,?,?)",
                            [(rid, float(r.t_s), r.step, r.tag, float(r.value)) for r in tr.itertuples()])
    return {"id": int(rid), "status": status, "alarms": alarms}


def runs(lot_id: str | None = None, status: str | None = None, source: str | None = None,
         limit: int = 200, dbpath=None) -> pd.DataFrame:
    q, p = "SELECT * FROM equipment_runs WHERE 1=1", []
    for col, v in (("lot_id", lot_id), ("status", status), ("source", source)):
        if v:
            q, p = q + f" AND {col}=?", p + [v]
    return db.read(q + f" ORDER BY id DESC LIMIT {int(limit)}", tuple(p), dbpath)


def run_detail(run_id: int, dbpath=None) -> dict | None:
    r = db.read("SELECT * FROM equipment_runs WHERE id=?", (int(run_id),), dbpath)
    if r.empty:
        return None
    return {
        "run": r.iloc[0].to_dict(),
        "summary": db.read("SELECT * FROM fdc_summary WHERE run_id=?", (int(run_id),), dbpath),
        "alarms": db.read("SELECT * FROM fdc_alarms WHERE run_id=?", (int(run_id),), dbpath),
        "trace": db.read("SELECT t_s, step, tag, value FROM equipment_trace WHERE run_id=? ORDER BY t_s",
                         (int(run_id),), dbpath),
    }


# ---------------------------------------------------------------- release

def _alarm_rows(dbpath=None, lot_id: str | None = None) -> pd.DataFrame:
    q = ("SELECT r.id, r.lot_id, r.equipment, a.step, a.message_ko, a.message_en FROM equipment_runs r "
         "JOIN fdc_alarms a ON a.run_id = r.id")
    return db.read(q + (" WHERE r.lot_id=?" if lot_id else "") + " ORDER BY r.id, a.step",
                   (lot_id,) if lot_id else (), dbpath)


def _flags(a: pd.DataFrame) -> list[tuple[str, str, str]]:
    level = _cfg().get("release_effect", "hold")
    out = []
    for (rid, eq), g in a.groupby(["id", "equipment"], sort=False):
        first = g.iloc[0]
        more_ko = f" 외 {len(g) - 1}건" if len(g) > 1 else ""
        more_en = f" (+{len(g) - 1} more)" if len(g) > 1 else ""
        out.append((level, f"설비 이상 이력 {eq} (run {rid}): {first['message_ko']}{more_ko}",
                    f"Equipment alarm {eq} (run {rid}): {first['message_en']}{more_en}"))
    return out


def flags(lot: str, dbpath=None) -> list[tuple[str, str, str]]:
    """(level, ko, en) for every alarmed run of the lot."""
    return _flags(_alarm_rows(dbpath, lot))


def flag_table(dbpath=None) -> dict[str, list]:
    a = _alarm_rows(dbpath)
    return {lot: _flags(g) for lot, g in a.groupby("lot_id")} if len(a) else {}
