"""Out-of-specification (OOS) results: keep the first result, investigate, never test into compliance.

Rules (in the spirit of the FDA OOS guidance, applied to an electronic-materials plant):
  * Every result stays on record. A repeat test is added, it never overwrites.
  * A passing retest alone does not release a lot whose first result failed.
  * A failing result can be set aside only when Phase 1 (laboratory) proves an assignable lab error.
  * If no lab error is found, the failure stands and Phase 2 (manufacturing) decides the disposition.
  * A repeat test needs a type (remeasure same sample / resample), a reason and an analyst, and is
    only accepted after an OOS investigation was opened for a failing result.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import config, db
from .insight.rules import lab, num

ATTEMPTS = ["remeasure", "resample"]
ATTEMPT_KO = {"initial": "최초", "remeasure": "재측정(같은 시료)", "resample": "재채취", None: "추가 측정(사유 미기록)"}
ATTEMPT_EN = {"initial": "Initial", "remeasure": "Re-measure (same sample)", "resample": "Re-sample",
              None: "Repeat (no reason recorded)"}
LAB_CAUSES = ["instrument", "analyst", "sample_prep", "calculation", "standard"]
LAB_CAUSE_KO = {"instrument": "장비 이상·교정", "analyst": "측정자 오류", "sample_prep": "시료 준비",
                "calculation": "계산·전기 오류", "standard": "표준물질·시약"}
DISPOSITIONS = ["scrap", "rework", "concession"]
DISPOSITION_KO = {"scrap": "폐기", "rework": "재작업 후 재검사", "concession": "고객 특채 요청"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _spec(prop: str) -> dict:
    return config.specs()["product"]["properties"][prop]


def is_oos(prop: str, v) -> bool:
    s = _spec(prop)
    if v is None or pd.isna(v):
        return False
    return (s.get("lsl") is not None and v < s["lsl"]) or (s.get("usl") is not None and v > s["usl"])


def history(lot: str, prop: str | None = None, dbpath=None) -> pd.DataFrame:
    """All in-house results for a lot, in test order, with judgement and the investigation that covers each."""
    h = db.product_history(lot, prop, dbpath)
    if h.empty:
        return h
    h["attempt"] = [a if isinstance(a, str) and a else ("initial" if s == 1 else None)
                    for a, s in zip(h["attempt"], h["seq"])]
    h["oos"] = [is_oos(p, v) for p, v in zip(h["property"], h["value"])]
    inv = investigations(dbpath)
    cover = {(r.lot_id, r.property, r.source_file): r.no for r in inv.itertuples()} if len(inv) else {}
    h["oos_no"] = [cover.get(k) for k in zip(h["lot_id"], h["property"], h["source_file"])]
    return h


def investigations(dbpath=None, open_only: bool = False) -> pd.DataFrame:
    q = "SELECT * FROM oos" + (" WHERE outcome='open'" if open_only else "") + " ORDER BY id DESC"
    return db.read(q, (), dbpath)


def investigation(no: str, dbpath=None) -> dict | None:
    r = db.read("SELECT * FROM oos WHERE no=?", (no,), dbpath)
    return r.iloc[0].to_dict() if len(r) else None


def uninvestigated(lot: str, prop: str, dbpath=None) -> pd.DataFrame:
    """Failing results of this lot/property that no investigation covers yet."""
    h = history(lot, prop, dbpath)
    return h[h["oos"] & ~h["invalidated"] & h["oos_no"].isna()] if len(h) else h


# ---------------------------------------------------------------- actions

def open_investigation(lot: str, prop: str, by: str, dbpath=None) -> str:
    """Open an OOS investigation on the first failing result that has none."""
    if not by.strip():
        raise ValueError("조사 담당자를 입력하세요 / investigator is required")
    u = uninvestigated(lot, prop, dbpath)
    if u.empty:
        raise ValueError("조사할 규격 이탈 결과가 없습니다 / no uninvestigated OOS result")
    r = u.iloc[0]
    with db.connect(dbpath) as con:
        n = con.execute("SELECT COUNT(*) FROM oos").fetchone()[0] + 1
        no = f"OOS-{datetime.now():%y%m}-{n:03d}"
        con.execute("INSERT INTO oos (no, lot_id, property, value, source_file, measured_at, opened, opened_by, "
                    "phase1, outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (no, lot, prop, float(r["value"]), r["source_file"], r["measured_at"], _now(), by.strip(),
                     "pending", "open"))
    return no


def add_retest(lot: str, prop: str, value: float, attempt: str, reason: str, operator: str,
               instrument: str = "", dbpath=None) -> int:
    """Record a repeat test next to the original. Returns the new measurement id."""
    if attempt not in ATTEMPTS:
        raise ValueError(f"attempt must be one of {ATTEMPTS}")
    if not reason.strip() or not operator.strip():
        raise ValueError("재시험 사유와 측정자를 입력하세요 / reason and analyst are required")
    if len(uninvestigated(lot, prop, dbpath)):
        raise ValueError("규격 이탈 결과에 OOS 조사를 먼저 등록해야 재시험할 수 있습니다 / "
                         "open an OOS investigation before retesting")
    if db.product_history(lot, prop, dbpath).empty:
        raise ValueError("최초 측정 결과가 없는 로트·항목입니다 / no initial result for this lot and property")
    at = _now()
    with db.connect(dbpath) as con:
        cur = con.execute(
            "INSERT INTO measurements (lot_id, material, property, value, unit, source, instrument, profile, "
            "operator, measured_at, source_file, attempt, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lot, "product", prop, float(value), _spec(prop)["unit"], "inhouse", instrument or "manual entry",
             "retest", operator.strip(), at, f"retest:{lot}:{prop}:{at}", attempt, reason.strip()))
        return cur.lastrowid


def record_phase1(no: str, lab_error: bool, cause: str, by: str, category: str | None = None, dbpath=None) -> None:
    """Phase 1 (laboratory). A proven lab error invalidates the failing result; otherwise it stands."""
    inv = investigation(no, dbpath)
    if not inv or inv["phase1"] != "pending":
        raise ValueError("1단계가 이미 종결된 조사입니다 / phase 1 already closed")
    if not cause.strip() or not by.strip():
        raise ValueError("조사 근거와 담당자를 입력하세요 / evidence and investigator are required")
    if lab_error and category not in LAB_CAUSES:
        raise ValueError(f"실험실 오류 유형을 고르세요 / category must be one of {LAB_CAUSES}")
    with db.connect(dbpath) as con:
        con.execute("UPDATE oos SET phase1=?, phase1_category=?, phase1_cause=?, phase1_by=?, phase1_at=?, "
                    "outcome=? WHERE no=?",
                    ("lab_error" if lab_error else "no_lab_error", category if lab_error else None, cause.strip(),
                     by.strip(), _now(), "invalidated" if lab_error else "open", no))


def record_phase2(no: str, finding: str, disposition: str, by: str, dbpath=None) -> None:
    """Phase 2 (manufacturing), only after Phase 1 found no lab error. The OOS is confirmed."""
    inv = investigation(no, dbpath)
    if not inv or inv["phase1"] != "no_lab_error" or inv["outcome"] != "open":
        raise ValueError("1단계에서 실험실 오류 없음으로 판정된 조사만 2단계로 갑니다 / "
                         "phase 2 follows a phase 1 with no lab error")
    if disposition not in DISPOSITIONS or not finding.strip() or not by.strip():
        raise ValueError("조사 결과·처리 방법·담당자를 입력하세요 / finding, disposition and owner are required")
    with db.connect(dbpath) as con:
        con.execute("UPDATE oos SET phase2_finding=?, disposition=?, phase2_by=?, phase2_at=?, outcome='confirmed' "
                    "WHERE no=?", (finding.strip(), disposition, by.strip(), _now(), no))


# ---------------------------------------------------------------- release

def flags(lot: str, dbpath=None, hist: pd.DataFrame | None = None) -> list[tuple[str, str, str]]:
    """(level, ko, en) findings from the test history that the latest values alone would hide."""
    h = history(lot, dbpath=dbpath) if hist is None else hist
    out = []
    if h is None or h.empty:
        return out
    inv = investigations(dbpath)
    inv = inv.set_index("no") if len(inv) else inv
    for prop, g in h.groupby("property", sort=False):
        valid = g[~g["invalidated"]]
        lk, le = lab(prop, "ko"), lab(prop, "en")
        # failing results that are not the reportable one (the reportable one is checked against spec)
        earlier = valid.iloc[:-1]
        for _, r in earlier[earlier["oos"]].iterrows():
            v = num(r["value"])
            if pd.isna(r["oos_no"]):
                out.append(("block", f"{lk} {r['seq']}차 결과 규격 이탈 {v}: 이후 합격 결과만으로 대체 불가, OOS 조사 필요",
                            f"{le} test {r['seq']} OOS {v}: a later pass cannot replace it, OOS investigation required"))
                continue
            i = inv.loc[r["oos_no"]]
            if i["outcome"] == "confirmed":
                d = DISPOSITION_KO.get(i["disposition"], i["disposition"])
                out.append(("block", f"{lk} OOS 확정 ({r['oos_no']}, 처리: {d})",
                            f"{le} OOS confirmed ({r['oos_no']}, disposition: {i['disposition']})"))
            elif i["phase1"] == "no_lab_error":
                out.append(("block", f"{lk} 실험실 오류 미확인, 최초 이탈 유효: 2단계 제조 조사 중 ({r['oos_no']})",
                            f"{le} no lab error found, OOS stands: phase 2 in progress ({r['oos_no']})"))
            else:
                out.append(("block", f"{lk} OOS 1단계 실험실 조사 진행 중 ({r['oos_no']}), 종결 전 출하 불가",
                            f"{le} OOS phase 1 lab investigation open ({r['oos_no']}): no release until closed"))
        for _, r in g[g["invalidated"]].iterrows():
            i = inv.loc[r["oos_no"]]
            c = LAB_CAUSE_KO.get(i["phase1_category"], i["phase1_category"])
            out.append(("release", f"{lk} {r['seq']}차 결과 {num(r['value'])} 무효 처리 ({r['oos_no']}, 실험실 오류: {c}), "
                                   "재시험 결과 채택",
                        f"{le} test {r['seq']} {num(r['value'])} invalidated ({r['oos_no']}, lab error: "
                        f"{i['phase1_category']}), retest reported"))
        if (g["seq"] > 1).any() and g["attempt"].isna().any():
            out.append(("hold", f"{lk} 사유가 기록되지 않은 추가 측정 있음", f"{le} repeat result without a recorded reason"))
    return out


def flag_table(dbpath=None) -> dict[str, list]:
    """flags() for every lot at once (one read)."""
    h = db.product_history(path=dbpath)
    if h.empty:
        return {}
    lots = set(h.groupby(["lot_id", "property"]).size().loc[lambda s: s > 1].index.get_level_values(0))
    lots |= set(investigations(dbpath)["lot_id"])
    return {lot: flags(lot, dbpath) for lot in lots}
