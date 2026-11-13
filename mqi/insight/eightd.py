"""8D report built from the evidence, in the structure customers and auditors expect.

Structure follows Ford Global 8D / VDA 8D practice:
  header with status per discipline · D0 symptom and emergency response · D1 team with roles ·
  D2 problem statement + 5W2H + IS / IS NOT · D3 containment by location with quantities and exit criteria ·
  D4 occurrence AND non-detection root causes (technical + systemic), 5-Why chains, 6M screening,
     verification evidence · D5 corrective actions for both causes with verification ·
  D6 implementation plan and validation criteria · D7 PFMEA / control plan / standards / horizontal
     deployment / lessons learned · D8 closure checklist.

Steps D0-D4 are filled from data. D5-D8 are proposals: owners are functions, dates are targets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import config, db, engine, qms
from ..analytics import changes as chg
from .rules import lab, num, snum

FUNCS = {
    "champion": ("품질보증팀장", "QA Manager"), "leader": ("SQE (공급사 품질)", "Supplier Quality Engineer"),
    "qc": ("품질관리(QC) 시험실", "QC Laboratory"), "pe": ("생산기술", "Process Engineering"),
    "prod": ("생산", "Production"), "pur": ("구매", "Purchasing"), "rd": ("연구소(배합)", "R&D Formulation"),
}


@dataclass
class EightD:
    lang: str
    no: str
    header: dict
    status: list
    d0: dict
    d1: list
    d2: dict
    d3: dict
    d4: dict
    d5: list
    d6: dict
    d7: dict
    d8: list
    notes: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    complaint: dict | None = None


def _L(lang, ko, en):
    return ko if lang == "ko" else en


def _f(key, lang):
    ko, en = FUNCS[key]
    return ko if lang == "ko" else en


def _d(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _limit_for(b: engine.Bundle, prop: str, feat: str) -> float | None:
    """Raw-material limit that keeps the product inside spec with margin, from the fitted model.

    Holds every other raw attribute at its median and finds where the prediction reaches
    USL (or LSL) minus 3 × model residual SD.
    """
    m = b.models.get(prop)
    if m is None or feat not in m.features:
        return None
    spec = config.specs()["product"]["properties"][prop]
    X = b.table[m.features].dropna()
    base = X.median().values
    j = m.features.index(feat)
    lo, hi = X[feat].quantile(0.001) * 0.8, X[feat].quantile(0.999) * 1.2
    grid = np.linspace(lo, hi, 400)
    G = np.tile(base, (len(grid), 1))
    G[:, j] = grid
    pred = m.model.predict(G)
    ok = np.ones(len(grid), bool)
    if spec.get("usl") is not None:
        ok &= pred <= spec["usl"] - 3 * m.resid_sd
    if spec.get("lsl") is not None:
        ok &= pred >= spec["lsl"] + 3 * m.resid_sd
    if not ok.any() or ok.all():
        return None
    # the boundary nearest to the current median on the failing side
    idx = np.flatnonzero(ok)
    cur = np.searchsorted(grid, base[j])
    edge = idx[0] if abs(idx[0] - cur) < abs(idx[-1] - cur) else idx[-1]
    return float(grid[edge])


def build(b: engine.Bundle, prop: str = "viscosity", lang: str = "ko", complaint: dict | None = None) -> EightD:
    """complaint: a row from qms.complaints() → customer-complaint 8D instead of an internal one."""
    L = lambda ko, en: _L(lang, ko, en)  # noqa: E731
    s = config.specs()["product"]
    spec = s["properties"][prop]
    unit = spec["unit"]
    x = b.product[prop]
    lots = b.product["lot_id"]
    first = b.spc[prop]["first"]
    alarm_i = min([v for k, v in first.items() if v is not None and k != "3σ (rule 1)"], default=None)
    alarm_lot = lots.iloc[alarm_i] if alarm_i is not None else lots.iloc[-1]
    alarm_date = str(b.product["measured_at"].iloc[alarm_i or 0])[:10]
    base, recent = x.iloc[:b.baseline], x.iloc[alarm_i:] if alarm_i is not None else x.tail(20)
    att = b.attribution.get(prop)
    top = att.iloc[0] if att is not None and len(att) else None
    sh = engine.instrument_shift(prop, b.dbpath, b=b)
    instrument = bool(sh and sh["p"] < 0.01 and abs(sh["diff"]) > 0.5 * sh["sd"]
                      and (top is None or abs(top["effect"]) < 0.5 * base.std()))
    detect_i = alarm_i
    if instrument:
        since = pd.to_datetime(sh["since"])
        hit = np.flatnonzero(pd.to_datetime(b.product["measured_at"]).values >= np.datetime64(since))
        if len(hit):
            alarm_i = int(hit[0])
            alarm_lot = lots.iloc[alarm_i]
            alarm_date = str(b.product["measured_at"].iloc[alarm_i])[:10]
            recent = x.iloc[alarm_i:]
    after = engine.lots_after(b, prop)
    share = engine.supplier_share(b, prop)
    trust = b.trust if b.trust is not None else pd.DataFrame()
    untrusted = trust[trust["trust"] == "untrusted"] if len(trust) else trust
    drv = top["feature"] if top is not None else None
    mat = next((m for m, d in config.specs()["raw_materials"].items() if drv in d["properties"]), None)
    sup = None
    if len(share):
        diff = (share["alarm"] - share["baseline"]).sort_values(ascending=False)
        sup = diff.index[0] if diff.iloc[0] > 0.05 else None
    cap = b.cap.get(prop)
    near = int((recent > (spec.get("usl", np.inf) - 0.1 * (spec["usl"] - spec["lsl"]))).sum()) if spec.get("usl") else 0
    oos = int(((x > (spec.get("usl") or np.inf)) | (x < (spec.get("lsl") or -np.inf))).sum())

    # ---------------- header
    no = f"8D-{date.today():%y%m}-{s['id']}-{prop[:4].upper()}"
    title = (L(f"{s['id']} {lab(prop, 'ko')} 상승 (규격 상한 접근)", f"{s['id']} {lab(prop, 'en')} rising toward USL")
             if recent.mean() > base.mean() else
             L(f"{s['id']} {lab(prop, 'ko')} 하락", f"{s['id']} {lab(prop, 'en')} falling"))
    if instrument:
        title = L(f"{s['id']} {lab(prop, 'ko')} 측정값 이동 (측정 장비 교체)",
                  f"{s['id']} {lab(prop, 'en')} reading shift (instrument change)")
    cc = complaint
    if cc:
        no = f"8D-{cc['no']}"
        title = L(f"고객 클레임 {cc['no']}: {cc['customer']} · {cc['symptom']}",
                  f"Customer complaint {cc['no']}: {cc['customer']} · {cc['symptom']}")
        cl = b.product[b.product["lot_id"] == cc["lot_id"]]
        cl_v = float(cl[prop].iloc[0]) if len(cl) else np.nan
        cl_pct = float((x < cl_v).mean() * 100) if len(cl) else np.nan
    header = {
        L("8D 번호", "8D No."): no, L("제목", "Title"): title,
        L("제품 / 규격", "Product / spec"): f"{s['name']} · {lab(prop, lang)} {spec.get('lsl')}–{spec.get('usl')} {unit}",
        L("유형", "Type"): (L(f"고객 클레임 ({cc['customer']}, 접수 {cc['received']}, 심각도 {cc['severity']})",
                              f"Customer complaint ({cc['customer']}, received {cc['received']}, severity {cc['severity']})") if cc else
                            L("사내 SPC 조기경보 (고객 클레임 전)", "Internal SPC early warning (before customer complaint)")),
        L("발행일", "Issued"): date.today().isoformat(), L("개정", "Revision"): "Rev.0",
        L("챔피언 / 팀장", "Champion / leader"): f"{_f('champion', lang)} / {_f('leader', lang)}",
    }
    status = [("D0", L("완료", "Done"), date.today().isoformat()), ("D1", L("완료", "Done"), date.today().isoformat()),
              ("D2", L("완료", "Done"), date.today().isoformat()), ("D3", L("진행", "Open"), _d(1)),
              ("D4", L("데이터 검증 완료 · 재현 시험 대기", "Verified on data · reproduction pending"), _d(14)),
              ("D5", L("계획", "Planned"), _d(21)), ("D6", L("계획", "Planned"), _d(45)),
              ("D7", L("계획", "Planned"), _d(60)), ("D8", L("미결", "Open"), _d(75))]

    # ---------------- D0
    o_new = n_new = ""
    if instrument:
        o_new, n_new = sh["old"].split(" · ")[0], sh["new"].split(" · ")[0]
    d0 = {"symptom": L(
        f"{alarm_lot}({alarm_date}) 이후 {lab(prop, 'ko')} 측정값이 {snum(recent.mean() - base.mean())} {unit} 이동. "
        f"같은 날 측정 장비가 {o_new} → {n_new}로 바뀜. 규격 이탈 {oos}건, Ppk {cap.ppk:.2f}.",
        f"From {alarm_lot} ({alarm_date}) {lab(prop, 'en')} readings moved {snum(recent.mean() - base.mean())} {unit}. "
        f"The instrument changed {o_new} → {n_new} that day. {oos} out of spec, Ppk {cap.ppk:.2f}.") if instrument else L(
        f"{alarm_lot}부터 {lab(prop, 'ko')} 평균이 {num(base.mean())} → {num(recent.mean())} {unit}로 이동. "
        f"규격 이탈은 {oos}건이지만 상한 10% 이내 근접 로트 {near}개. Ppk {cap.ppk:.2f} (목표 1.33).",
        f"From {alarm_lot}, mean {lab(prop, 'en')} moved {num(base.mean())} → {num(recent.mean())} {unit}. "
        f"{oos} out of spec, {near} lots within 10% of USL. Ppk {cap.ppk:.2f} (target 1.33)."),
        "era0": None,
        "era": L("보관 샘플을 기존 장비로 재측정해 실제 변화 여부 확인 (판정 전 출하 판정 보류)",
                 "Re-test retained samples on the old instrument before further release decisions") if instrument else
               L("영향 로트 출하 보류 및 보관 샘플 재측정 지시 (D3에서 범위 확정)",
                 "Hold shipment of affected lots and re-test retained samples (scope fixed in D3)"),
        "why_8d": L("원인 미상 · 다부서 협업 필요 · 고객 영향 가능성 → 8D 착수",
                    "Cause unknown, cross-functional, possible customer impact → 8D opened")}

    if cc:
        d0["symptom"] = L(
            f"고객 표현: \"{cc['symptom']}\" (로트 {cc['lot_id']}, {num(cc['qty'])} {cc['unit']}). "
            f"출하 시 {lab(prop, 'ko')} {num(cl_v)} {unit} (규격 내, 전체 로트 중 {cl_pct:.0f} 백분위). ",
            f"Customer: \"{cc['symptom']}\" (lot {cc['lot_id']}, {num(cc['qty'])} {cc['unit']}). "
            f"Released at {lab(prop, 'en')} {num(cl_v)} {unit} (in spec, {cl_pct:.0f}th percentile of all lots). ") + d0["symptom"]
        d0["era"] = L("24시간 내: 고객 재고 동일 로트 사용 중지 요청 · 대체품 출하 · 보관 샘플 재측정",
                      "Within 24 h: ask customer to stop using the lot, ship replacement, re-test retained sample")
        d0["why_8d"] = L("고객 클레임 → 8D 회신 의무 (D3 24시간, D4 2주)", "Customer complaint → 8D response due (D3 24 h, D4 2 weeks)")
    del d0["era0"]

    # ---------------- D1
    d1 = [(_f("champion", lang), L("챔피언 · 자원 승인", "Champion · resources")),
          (_f("leader", lang), L("팀장 · 공급사 대응 · 8D 작성", "Leader · supplier contact · report")),
          (_f("qc", lang), L("재측정 · 측정시스템 검증", "Re-test · measurement system")),
          (_f("pe", lang), L("공정 조건 검토 · 원료-제품 영향 분석", "Process review · raw-to-product analysis")),
          (_f("prod", lang), L("재고·재공 격리 · 배치 순서 조정", "Quarantine stock/WIP · batch sequencing")),
          (_f("pur", lang), L("공급사 SCAR · 대체 공급 검토", "SCAR · second source")),
          (_f("rd", lang), L("배합 허용 범위 검토", "Formulation window"))]

    # ---------------- D2
    drop = L(f"공정능력이 Ppk {cap.ppk:.2f}로 저하됨", f"capability fell to Ppk {cap.ppk:.2f}") if cap.ppk < 1.33 else \
        L(f"Ppk {cap.ppk:.2f} (목표 이상이나 추세 지속 시 위험)", f"Ppk {cap.ppk:.2f} (above target, at risk if it continues)")
    stmt = L(f"{s['id']} {lab(prop, 'ko')}가 {alarm_date}({alarm_lot}) 이후 평균 {snum(recent.mean() - base.mean())} {unit} "
             f"이동, {drop} (영향 {len(after)}로트).",
             f"{s['id']} {lab(prop, 'en')} has shifted {snum(recent.mean() - base.mean())} {unit} on average since "
             f"{alarm_date} ({alarm_lot}); {drop} ({len(after)} lots affected).")
    w5h2 = [
        ("What", L(f"{lab(prop, 'ko')} 측정값 단차 발생", f"Step change in {lab(prop, 'en')} readings") if instrument else
                 L(f"{lab(prop, 'ko')} 평균 상승, 규격 상한 접근", f"{lab(prop, 'en')} mean rising toward USL")),
        ("Where", L("전 생산 라인 제품 로트 (출하검사 시험실 측정)", "All production lots (release testing lab)")),
        ("When", L(f"{alarm_date} 이후 지속, 최근 10로트 평균 {num(x.tail(10).mean())}", f"Since {alarm_date}, ongoing; last 10 lots {num(x.tail(10).mean())}")),
        ("Who", L(f"고객 {cc['customer']} 수입/공정에서 발견, {cc['received']} 접수" if cc else "", f"Found by {cc['customer']}, received {cc['received']}" if cc else "")
                if cc else L("사내 SPC 조기경보(EWMA/Nelson)로 품질팀이 인지", "Detected by QA through SPC early warning (EWMA/Nelson)")),
        ("How", L(f"기준 대비 {snum(recent.mean() - base.mean())} {unit} 단계적 상승, 3σ보다 {first['3σ (rule 1)'] - detect_i if first['3σ (rule 1)'] and detect_i is not None else '-'}로트 먼저 감지",
                  f"Stepwise rise of {snum(recent.mean() - base.mean())} {unit}; caught {first['3σ (rule 1)'] - detect_i if first['3σ (rule 1)'] and detect_i is not None else '-'} lots before 3σ")),
        ("How many", L(f"영향 {len(after)}로트 / 규격 이탈 {oos} / 상한 근접 {near}", f"{len(after)} lots affected / {oos} OOS / {near} near USL")),
    ]
    if cc:
        w5h2.insert(0, ("Customer", L(f"{cc['customer']} · 로트 {cc['lot_id']} · {num(cc['qty'])} {cc['unit']} · {cc['symptom']}",
                                      f"{cc['customer']} · lot {cc['lot_id']} · {num(cc['qty'])} {cc['unit']} · {cc['symptom']}")))
    isnot = []
    if cc and len(cl):
        isnot.append((L("어느 로트", "Which lot"), f"{cc['lot_id']}: {num(cl_v)}",
                      L(f"기준 구간 평균 {num(base.mean())}", f"baseline mean {num(base.mean())}"),
                      L(f"규격 내, {cl_pct:.0f} 백분위 → 고객 공정 허용 범위가 출하 규격보다 좁을 가능성", f"In spec, {cl_pct:.0f}th percentile → customer window may be tighter than the spec")))
    other = [p for p in b.cap if p != prop]
    stable = [p for p in other if b.spc[p]["first"]["EWMA"] is None and b.spc[p]["first"]["CUSUM"] is None]
    isnot.append((L("무엇", "What"), L(f"{lab(prop, 'ko')}", f"{lab(prop, 'en')}"),
                  ", ".join(lab(p, lang) for p in stable) or "-",
                  L("특정 물성만 변화 → 경화 반응보다 유동성(필러) 쪽 요인", "Only one property moves → flow (filler) rather than cure")
                  if not instrument else L("DSC 측정 항목만 변화", "Only DSC-measured properties move")))
    if instrument:
        isnot.append((L("어떤 장비", "Which instrument"), f"{n_new}", f"{o_new}",
                      L("장비 교체 시점과 정확히 일치", "Coincides exactly with the instrument change")))
    elif sup and mat and len(after):
        col = f"{mat}_supplier"
        a_sup = after.groupby(col)[prop].mean()
        isnot.append((L("어디 (원료)", "Where (raw material)"),
                      f"{sup}: {num(a_sup.get(sup, np.nan))}",
                      ", ".join(f"{k}: {num(v)}" for k, v in a_sup.items() if k != sup),
                      L(f"{sup} 원료 사용 로트에서만 높음", f"High only on lots using {sup}")))
    isnot.append((L("언제", "When"), L(f"{alarm_date} 이후", f"after {alarm_date}"),
                  L(f"기준 구간 (처음 {b.baseline}로트)", f"baseline (first {b.baseline} lots)"),
                  L("특정 시점부터 시작 → 변경점(4M) 추적", "Starts at a point in time → trace 4M changes")))
    near_c = chg.changes_near(b, prop, alarm_date)
    sd0 = base.std()
    clist = []
    for _, c in near_c.iterrows():
        hit = (not pd.isna(c["p"])) and c["p"] < 0.05 and abs(c["resid_step"]) > 0.5 * sd0
        ev = f"{snum(c['resid_step'])} (p={c['p']:.2f})" if not pd.isna(c["p"]) else "-"
        if c["category"] == "Measurement" and sh and abs((pd.to_datetime(sh["since"]) - c["date"]).days) <= 3:
            hit = sh["p"] < 0.01 and abs(sh["diff"]) > 0.5 * sh["sd"]
            ev = f"ANCOVA {snum(sh['diff'])} (p={sh['p']:.1e})"
        clist.append((c["change_no"], str(c["date"])[:10], c["category"], f"{c['item']}: {c['description']}", ev,
                      L("원인 후보", "Candidate") if hit else L("제외", "Excluded")))
    sup_changed = bool(len(near_c) and sup and near_c["item"].str.contains(sup, case=False).any())
    m_op = db.product_results(prop, b.dbpath)[["lot_id", "operator", "value"]]
    m_op = m_op[m_op["lot_id"].isin(after["lot_id"])] if len(after) else m_op
    if m_op["operator"].nunique() > 1:
        ops = m_op.groupby("operator")["value"].mean()
        isnot.append((L("누가 (측정자)", "Who (operator)"), L("모든 측정자에서 상승", "All operators see it"),
                      L("특정 측정자 편중 없음", "No single operator"),
                      L("측정자 범위 " + f"{num(ops.min())}–{num(ops.max())} → 측정자 요인 제외",
                        "Operator means " + f"{num(ops.min())}–{num(ops.max())} → operator excluded")))

    # ---------------- D3
    made = pd.to_datetime(b.product["measured_at"])
    cutoff = made.max() - pd.Timedelta(days=28)
    aff = b.product[b.product["lot_id"].isin(after["lot_id"])]
    recent_aff = aff[pd.to_datetime(aff["measured_at"]) >= cutoff]
    older_aff = aff[pd.to_datetime(aff["measured_at"]) < cutoff]
    hi_risk = aff[aff[prop] > (spec.get("usl", np.inf) - 0.1 * (spec["usl"] - spec["lsl"]))] if spec.get("usl") else aff.iloc[:0]
    raw_lots = db.read("SELECT lot_id, supplier, made_at FROM lots WHERE material=?", (mat,), b.dbpath) if mat else pd.DataFrame()
    if len(raw_lots) and sup:
        raw_lots["made_at"] = pd.to_datetime(raw_lots["made_at"])
        sus_raw = raw_lots[(raw_lots["supplier"] == sup) & (raw_lots["made_at"] >= raw_lots["made_at"].max() - pd.Timedelta(days=30))]
    else:
        sus_raw = pd.DataFrame(columns=["lot_id"])
    if instrument:
        d3 = {"rows": [
            (L("시험실 보관 샘플", "Retained samples"), f"10 {L('개', 'samples')}", L("기존·신규 장비 동시 측정으로 차이 확인", "Measure on both instruments"), _f("qc", lang), _d(2)),
            (L("교체 후 판정 로트", "Lots released after change"), f"{len(after)} {L('로트', 'lots')}",
             L("보정값 적용 후 재판정 (규격 경계 로트 우선)", "Re-judge with offset applied (borderline lots first)"), _f("qc", lang), _d(5)),
            (L("신규 측정", "New measurements"), "-", L(f"상관성 확인 전까지 {o_new} 병행 측정", f"Run {o_new} in parallel until correlated"), _f("qc", lang), _d(1)),
        ], "verify": L("병행 측정 차이가 ±0.5 ℃ 이내인지 확인", "Parallel results within ±0.5 °C"),
              "exit": L("D5 보정·재교정 완료 및 병행 측정 10건 일치 시 해제", "Lift after correction and 10 matching parallel results")}
    else:
        d3 = {"rows": [
          (L("원료 재고", "Raw material stock"), f"{len(sus_raw)} {L('로트', 'lots')} ({', '.join(sus_raw['lot_id'].tolist()[:4])})" if len(sus_raw) else "-",
           L("격리 · 사용 전 D50 재측정 · 위험도 낮은 조합 우선 사용", "Quarantine · re-test D50 · use low-risk combinations first"),
           _f("qc", lang), _d(1)),
          (L("재공 / 창고 (최근 4주)", "WIP / warehouse (last 4 weeks)"), f"{len(recent_aff)} {L('로트', 'lots')}",
           L("출하 보류 · 보관 샘플 재측정 · 상한 근접 로트 별도 판정", "Hold · re-test retained samples · separate decision on near-USL lots"),
           _f("prod", lang), _d(1)),
          (L("출하분 (4주 이전)", "Shipped (older than 4 weeks)"), f"{len(older_aff)} {L('로트', 'lots')}",
           L("출하 이력 추적 · 상한 근접 로트 고객 통보 여부 판단", "Trace shipments · decide customer notice for near-USL lots"),
           _f("leader", lang), _d(2)),
          (L("고객사", "Customer"), f"{len(hi_risk)} {L('로트 (상한 10% 이내)', 'lots within 10% of USL')}",
           L("필요 시 고객 사전 통보 및 사용 이력 확인", "Notify customer if required; check usage"), _f("champion", lang), _d(2)),
        ],
          "verify": L("격리 이후 출하 로트 전수 재측정, 결과 규격 내 확인 시 해제", "100% re-test of every lot released after hold"),
          "exit": L("D6 유효성 검증 완료 시 (연속 25로트 Ppk ≥ 1.33) 봉쇄 해제", "Lift when D6 validation passes (25 lots Ppk ≥ 1.33)")}

    if cc:
        d3["rows"].insert(0, (L(f"고객 재고 ({cc['customer']})", f"Customer stock ({cc['customer']})"),
                              f"{num(cc['qty'])} {cc['unit']} ({cc['lot_id']})",
                              L("사용 중지 · 회수 또는 선별 · 대체품 긴급 출하", "Stop use · return or sort · ship replacement"),
                              _f("leader", lang), _d(1)))

    # ---------------- D4
    r2 = b.models[prop].r2 if prop in b.models else float("nan")
    limit = _limit_for(b, prop, drv) if drv else None
    rspec = config.all_property_specs().get(drv, {}) if drv else {}
    if instrument:
        o_new, n_new = sh["old"].split(" · ")[0], sh["new"].split(" · ")[0]
        occ_trc = L(f"측정 장비 교체({o_new} → {n_new}, {sh['since']}) 후 측정값이 {snum(sh['diff'])} {unit} 높게 나옴. 제품 자체 변화 아님",
                    f"After the instrument change ({o_new} → {n_new}, {sh['since']}) readings are {snum(sh['diff'])} {unit} higher; the product did not change")
        occ_mrc = L("장비 도입 시 기존 장비와의 상관성(Bias) 평가가 절차에 없음", "No correlation (bias) study required when introducing a new instrument")
        nd_trc = L("관리한계를 장비 교체 전 데이터로 유지해 측정 이동이 공정 이동처럼 보임", "Control limits kept from the old instrument, so a reading shift looks like a process shift")
        nd_mrc = L("측정 시스템 변경이 4M 변경관리 대상에 포함되지 않음", "Measurement changes not covered by 4M change control")
        why_o = [L(f"{lab(prop, 'ko')} 측정값이 올라감", f"{lab(prop, 'en')} readings went up"),
                 L(f"원료 영향을 통제하면 장비 교체 시점에서만 {snum(sh['diff'])} 단차 (ANCOVA p={sh['p']:.1e})", f"With raw materials controlled, a {snum(sh['diff'])} step at the instrument change (ANCOVA p={sh['p']:.1e})"),
                 L("두 장비의 베이스라인·교정 방식 차이", "Different baseline / calibration between instruments"),
                 L("도입 시 상관성 평가 미실시", "No correlation study at introduction")]
        why_n = [L("SPC가 이동을 공정 변화로 판단", "SPC read it as a process change"),
                 L("장비별 층별 관리를 하지 않음", "Data not stratified by instrument"),
                 L("측정 변경이 변경관리 대상이 아님", "Measurement change outside change control")]
    else:
        occ_trc = (L(f"원료 {lab(drv, 'ko')} 변화 {num(top['baseline'])} → {num(top['alarm'])} {rspec.get('unit', '')}"
                     + (f" ({sup} 로트)" if sup else "") + f" → 예측 영향 {snum(top['effect'])} {unit}",
                     f"Raw {lab(drv, 'en')} changed {num(top['baseline'])} → {num(top['alarm'])} {rspec.get('unit', '')}"
                     + (f" ({sup} lots)" if sup else "") + f" → predicted effect {snum(top['effect'])} {unit}")
                   if top is not None else L("데이터로 특정되지 않음", "Not identified from data"))
        occ_mrc = L(f"원료 규격({rspec.get('lsl')}–{rspec.get('usl')})이 제품 영향 기준으로 설정되지 않음: 규격 안에서의 이동도 제품을 움직임",
                    f"Raw spec ({rspec.get('lsl')}–{rspec.get('usl')}) is not derived from product impact: drift inside spec still moves the product")
        nd_trc = L("수입검사가 규격 합/부만 판정 · 규격 내 추세(공급사별 이동)를 보지 않음",
                   "Incoming inspection is pass/fail only; trends inside spec per supplier are not watched")
        if len(untrusted):
            u = untrusted.iloc[0]
            nd_trc += L(f" · {u['supplier']} CoA가 실제보다 높게 기재되어 이동을 가림 ({u['drift_since']}부터 차이 확대)",
                        f" · {u['supplier']} CoA overstated the value and masked the drift (gap growing since {u['drift_since']})")
        nd_mrc = L("CoA 신뢰도 정기 검증과 원료 SPC가 수입검사 기준서에 없음", "No periodic CoA verification or raw-material SPC in the incoming inspection standard")
        if sup and not sup_changed:
            occ_mrc += L(f" · {sup}의 공정 변경이 4M 변경 이력에 없음 (사전 통보 없음)", f" · {sup}'s process change never reached the 4M change log (no notification)")
        why_o = [L(f"{lab(prop, 'ko')}가 올라감", f"{lab(prop, 'en')} went up"),
                 (L(f"필러 {lab(drv, 'ko')}가 작아져 비표면적·입자 간 마찰 증가 (모델 영향 1순위, R²={r2:.2f})",
                    f"Smaller filler {lab(drv, 'en')} raises surface area and particle friction (top model driver, R²={r2:.2f})")
                  if drv == "d50" else
                  L(f"원료 {lab(drv, 'ko')} 변화가 모델 영향 1순위 (R²={r2:.2f})", f"Raw {lab(drv, 'en')} is the top model driver (R²={r2:.2f})"))
                 if drv else L("원료 요인 미특정", "No raw driver identified"),
                 L(f"{sup} 로트의 {lab(drv, 'ko')}가 단계적으로 감소", f"{sup} lots show a stepwise {lab(drv, 'en')} decrease") if sup else "-",
                 L("공급사 분급(classification) 공정 변경 추정 → SCAR로 확인 필요", "Supplier classification change suspected → confirm by SCAR"),
                 L("원료 규격이 제품 영향 기준이 아니어서 규격 내 변화가 허용됨", "Raw spec not based on product impact, so in-spec change was allowed")]
        why_n = [L("수입검사에서 합격 처리됨", "Accepted at incoming inspection"),
                 L(f"측정값이 원료 규격({rspec.get('lsl')}–{rspec.get('usl')}) 안에 있었음", f"Values were inside the raw spec ({rspec.get('lsl')}–{rspec.get('usl')})"),
                 L("공급사별 추세 관리(SPC) 없음 · CoA 수치 의존", "No supplier-level trend control; relied on CoA"),
                 L("CoA-자사 측정 상관성 정기 검증 절차 없음", "No periodic CoA-vs-in-house correlation check")]
    m6 = []
    if instrument:
        m6.append(("Material", L("원료 변화", "Raw material change"),
                   L("ANCOVA로 원료 영향 통제 후에도 단차 유지", "Step remains after controlling raw materials"), L("제외", "Excluded")))
    else:
        m6.append(("Material", L(f"필러 {lab(drv, 'ko')} 감소 ({sup})", f"Filler {lab(drv, 'en')} decrease ({sup})") if drv else "-",
                   L(f"모델 영향 {snum(top['effect'])}, 공급사 비중 변화", f"Model effect {snum(top['effect'])}, supplier share") if top is not None else "-",
                   L("확인", "Verified")))
    g = b.gage
    m6.append(("Measurement", L(f"측정 장비 교체 ({o_new} → {n_new})", f"Instrument change ({o_new} → {n_new})") if instrument else
               L(f"점도계 %GRR {g.grr_pct_sv:.1f}%, 장비 교체 영향", f"Viscometer %GRR {g.grr_pct_sv:.1f}%, instrument change") if g else "-",
               L(f"장비 편차 {snum(sh['diff'])} {unit} (p={sh['p']:.3f})", f"Instrument offset {snum(sh['diff'])} {unit} (p={sh['p']:.3f})") if sh else "-",
               L("확인", "Verified") if instrument else L("제외 (이동 폭 대비 작음)", "Excluded (small vs shift)")))
    m6.append(("Man", L("측정자·작업자 차이", "Operator differences"),
               L("모든 측정자에서 동일 경향", "Same trend for all operators"), L("제외", "Excluded")))
    m6.append(("Method", L("장비 변경 시 상관성 평가 절차 없음", "No correlation procedure at instrument change"),
               L("변경관리 대상 아님", "Outside change control"), L("확인 (유출 원인)", "Verified (escape)")) if instrument else
              (L("Method (검사)", "Method (inspection)"), L("수입검사 합/부 판정만 수행", "Incoming check is pass/fail only"),
               L("규격 내 이동 미관리", "In-spec drift not tracked"), L("확인 (유출 원인)", "Verified (escape)")))
    ps = chg.process_screen(b, prop, alarm_i)
    if len(ps):
        mach = ps[ps["param"].isin(["mixer", "vacuum", "degas_temp"])]
        meth = ps[ps["param"].isin(["mix_time", "batch_kg"])]

        def _ev(d):
            return " · ".join(f"{chg.PARAMS[r.param][0 if lang == 'ko' else 1]} p={r.p_resid:.2f}" for r in d.itertuples())

        m6.append(("Machine", L("믹서(M1/M2)·탈포 진공도·온도", "Mixer (M1/M2), vacuum, degas temp"),
                   L("MES 공정 기록 vs 원료로 설명 안 되는 편차: ", "MES record vs unexplained variation: ") + _ev(mach),
                   L("확인", "Verified") if mach["linked"].any() else L("제외", "Excluded")))
        m6.append((L("Method (공정)", "Method (process)"), L("믹싱 시간·배치 크기", "Mix time, batch size"),
                   L("MES 공정 기록: ", "MES record: ") + _ev(meth),
                   L("확인", "Verified") if meth["linked"].any() else L("제외", "Excluded")))
    else:
        m6.append(("Machine", L("믹서·탈포 조건 변화", "Mixer / degassing settings"),
                   L("설비 데이터 없음 · 공정 조건 기록 확인 필요", "No equipment data · check batch records"), L("미검증", "Not verified")))
    m6.append(("Environment", L("시험실 온도(25℃) 관리", "Lab temperature (25 °C)"),
               L("측정 온도 25.0℃ 기록 일정", "Logged 25.0 °C throughout"), L("제외", "Excluded")))
    verify = []
    if not instrument and top is not None and prop in b.models:
        verify.append(L(f"원료→제품 모델: 교차검증 R² {r2:.2f}, {lab(drv, 'ko')} 단독으로 이동량 {snum(top['effect'])} 설명 "
                        f"(실제 이동 {snum(recent.mean() - base.mean())})",
                        f"Raw→product model: CV R² {r2:.2f}; {lab(drv, 'en')} alone explains {snum(top['effect'])} "
                        f"(actual shift {snum(recent.mean() - base.mean())})"))
        if len(share):
            verify.append(L("필러 공급사 비중 (기준→이동 구간): ", "Filler supplier share (baseline→shift): ")
                          + ", ".join(f"{k} {v.baseline:.0%}→{v.alarm:.0%}" for k, v in share.iterrows()))
        for _, u in untrusted.iterrows():
            verify.append(L(f"{u['supplier']} CoA vs 자사 {lab(u['property'], 'ko')}: 평균 차이 {snum(u['bias'])}, 차이 확대 시작 {u['drift_since']}",
                            f"{u['supplier']} CoA vs in-house {lab(u['property'], 'en')}: mean gap {snum(u['bias'])}, growing since {u['drift_since']}"))
        verify.append(L("재현 시험(On/Off): 기존 D50 로트와 저D50 로트로 동일 배합 비교 → 수행 예정",
                        "Reproduction (on/off): same formulation with normal vs low-D50 filler → planned"))
    elif instrument:
        verify.append(L(f"ANCOVA: 원료 영향 통제 후 장비 계수 {snum(sh['diff'])} {unit}, p={sh['p']:.1e}",
                        f"ANCOVA: instrument term {snum(sh['diff'])} {unit} after controlling raw materials, p={sh['p']:.1e}"))
        verify.append(L("재현 시험: 보관 샘플 10개를 두 장비로 측정 → 수행 예정", "Reproduction: 10 retained samples on both instruments → planned"))
    d4 = {"occ_trc": occ_trc, "occ_mrc": occ_mrc, "nd_trc": nd_trc, "nd_mrc": nd_mrc,
          "why_o": [w for w in why_o if w != "-"], "why_n": why_n, "m6": m6, "verify": verify}

    # ---------------- D5 / D6
    if instrument:
        d5 = [(L("발생", "Occurrence"), L("두 장비 상관성 평가 후 보정식 적용 또는 신규 장비 재교정", "Correlation study; apply offset or recalibrate the new instrument"),
               L("보관 샘플 10개 두 장비 차이 ≤ 0.5 ℃", "10 retained samples: |Δ| ≤ 0.5 °C"), _f("qc", lang), _d(14)),
              (L("미검출", "Non-detection"), L("측정 장비 변경을 4M 변경관리 대상에 추가 · 장비별 층별 SPC", "Add instrument changes to 4M change control; stratify SPC by instrument"),
               L("변경관리 절차 개정 · 시스템 반영", "Procedure revised · system updated"), _f("leader", lang), _d(21))]
    else:
        lim_txt = (L(f"{lab(drv, 'ko')} 하한 {num(limit)} {rspec.get('unit', '')} (현행 {rspec.get('lsl')})",
                     f"{lab(drv, 'en')} lower limit {num(limit)} {rspec.get('unit', '')} (now {rspec.get('lsl')})")
                   if limit is not None else L("제품 영향 기준 원료 한계 재설정", "Reset raw limit from product impact"))
        d5 = [(L("발생", "Occurrence"), L(f"{sup} SCAR: 분급 조건 원복 및 출하 기준 강화", f"SCAR to {sup}: restore classification and tighten release")
               if sup else L("공급사 원인 조치", "Supplier root-cause action"),
               L("공급사 3로트 연속 자사 측정 신규 한계 이상", "3 consecutive supplier lots inside the new limit (in-house)"), _f("pur", lang), _d(21)),
              (L("발생", "Occurrence"), L("배치 전 원료 조합 위험도 확인 후 투입 (앱 '재고 원료 조합 위험도')", "Pre-batch risk check on raw-lot combination"),
               L("예측 이탈확률 5% 초과 조합 투입 0건", "0 batches started above 5% predicted risk"), _f("prod", lang), _d(7)),
              (L("미검출", "Non-detection"), L(f"수입검사 원료 한계를 제품 영향 기준으로 재설정: {lim_txt}", f"Reset incoming limit from product impact: {lim_txt}"),
               L("모델 예측 제품 이탈확률 < 0.3% 유지", "Predicted product OOS < 0.3%"), _f("leader", lang), _d(14)),
              (L("미검출", "Non-detection"), L("공급사별 원료 EWMA 관리도 + 월 1회 CoA 신뢰도 검증, 신뢰 불가 시 Skip-lot 중지",
                                              "Supplier-level EWMA on raw data + monthly CoA trust check; no skip-lot when untrusted"),
               L("규격 내 이동 3로트 이내 경보", "Alarm within 3 lots of an in-spec drift"), _f("qc", lang), _d(14))]
    reg = qms.actions(no, b.dbpath)
    st_of = dict(zip(reg["action"], reg["status"])) if len(reg) else {}
    sl = (lambda v: qms.STATUS_KO.get(v, v)) if lang == "ko" else (lambda v: v)
    d6 = {"rows": [(a, act, owner, due, sl(st_of.get(act, "Open")) if st_of else L("미등록", "Not registered"))
                   for a, act, _, owner, due in d5],
          "criteria": [L("보정 후 기준 구간과 평균 차이 ±0.5 ℃ 이내", "After correction, mean within ±0.5 °C of baseline")] if instrument else [L(f"조치 후 연속 25로트 {lab(prop, 'ko')} Ppk ≥ 1.33", f"25 consecutive lots after action: {lab(prop, 'en')} Ppk ≥ 1.33"),
                       L("EWMA·CUSUM 경보 없음", "No EWMA / CUSUM alarm"),
                       L("CoA-자사 차이 ±(규격 반폭의 10%) 이내", "CoA vs in-house within ±10% of half-spec")]}

    # ---------------- D7
    if instrument:
        fmea = [(L("측정값 오판정", "Wrong release decision"), L("측정 장비 간 Bias", "Bias between instruments"),
                 L("장비 도입 시 상관성 평가", "Correlation study at introduction"), "6 → 2", "6 → 3")]
        cp = [(lab(prop, lang), "DSC", L("장비 변경 시 + 연 1회", "At change + yearly"), L("동일 샘플 상관성 평가", "Same-sample correlation"))]
    else:
        fmea = [(L(f"{lab(prop, 'ko')} 규격 상한 이탈", f"{lab(prop, 'en')} above USL"),
                 L(f"필러 {lab(drv, 'ko')} 감소 (공급사 공정 변경)", f"Filler {lab(drv, 'en')} decrease (supplier change)"),
                 L("원료 SPC + 제품 영향 기준 한계", "Raw SPC + product-impact limit"), "5 → 3", "7 → 3")]
        cp = [(f"{config.specs()['raw_materials'][mat]['label']} {lab(drv, lang)}" if mat else "-", "Laser diffraction",
               L("매 로트", "Every lot"), L(f"신규 하한 {num(limit)} 미만 불합격 · EWMA 경보 시 공급사 통보", f"Reject below {num(limit)}; notify supplier on EWMA alarm") if limit else "-"),
              ("CoA", L("자사 측정 대조", "In-house comparison"), L("월 1회", "Monthly"),
               L("신뢰 불가 시 Skip-lot 중지", "Stop skip-lot when untrusted"))]
    others = []
    if len(trust):
        others = trust[(trust["trust"] != "trusted")]["supplier"].unique().tolist()
    d7 = {"fmea": fmea, "cp": cp,
          "docs": [L("수입검사 기준서 (원료 한계·SPC·CoA 검증 추가)", "Incoming inspection standard (limits, SPC, CoA check)"),
                   L("공급사 품질 협약서 (변경 사전 통보 조항)", "Supplier quality agreement (change notification clause)"),
                   L("4M 변경관리 절차 (측정 장비 포함)", "4M change control (include instruments)")],
          "horizontal": [L("같은 필러를 쓰는 다른 제품 점검", "Check other products using the same filler"),
                         L("전 원료 공급사 CoA 신뢰도 일괄 점검: ", "CoA trust review of all suppliers: ") + (", ".join(others) if others else "-"),
                         L("다른 원료 규격도 제품 영향 기준으로 재검토 (모델 기반)", "Review other raw specs against product impact")],
          "lesson": L("규격 안의 원료 변화도 제품을 움직인다. 원료 규격은 제품 영향으로 정하고, CoA는 정기적으로 검증한다.",
                      "In-spec raw variation still moves the product. Set raw limits from product impact and verify CoA regularly.")}

    d8 = [L("D6 유효성 검증 완료 (25로트)", "D6 validation complete (25 lots)"),
          L("D7 문서 개정·교육 완료", "D7 documents revised and trained"),
          L("고객 통보 건 종결 확인", "Customer notices closed"),
          L("봉쇄 해제 승인", "Containment lifted"),
          L("팀 성과 공유 및 8D 종결 승인 (챔피언)", "Team recognised; closure signed by champion")]

    notes = [L("D0–D4는 데이터로 작성, D5–D8은 제안 (담당은 기능 단위, 일자는 목표일).",
               "D0–D4 written from data; D5–D8 are proposals (owners are functions, dates are targets).")]
    if st_of:
        done = sum(st_of.get(a[1]) in ("Done", "Verified") for a in d5)
        ver = sum(st_of.get(a[1]) == "Verified" for a in d5)
        status[5] = ("D5", L(f"실행 {done}/{len(d5)}", f"{done}/{len(d5)} done"), status[5][2])
        status[6] = ("D6", L(f"유효성 확인 {ver}/{len(d5)}", f"{ver}/{len(d5)} verified"), status[6][2])
        c3 = [st_of.get(r[2]) for r in d3["rows"]]
        if c3 and all(v in ("Done", "Verified") for v in c3):
            status[3] = ("D3", L("완료", "Done"), status[3][2])
    if cc:
        status[3] = (status[3][0], status[3][1], _d(1))
    return EightD(lang, no, header, status, d0, d1, {"stmt": stmt, "w5h2": w5h2, "isnot": isnot}, d3, d4, d5, d6, d7, d8, notes, clist, cc)


def register_rows(r: EightD) -> list[dict]:
    """Containment and corrective actions as rows for the action register."""
    out = [dict(step="D3", kind=row[0], action=row[2], verification=r.d3["verify"], owner=row[3], due=row[4])
           for row in r.d3["rows"]]
    out += [dict(step="D5", kind=row[0], action=row[1], verification=row[2], owner=row[3], due=row[4]) for row in r.d5]
    return out


# ============================================================ renderers

def _t(rows, head):
    out = "| " + " | ".join(head) + " |\n|" + "---|" * len(head) + "\n"
    for r in rows:
        out += "| " + " | ".join(str(c).replace("|", "/") for c in r) + " |\n"
    return out


def markdown(r: EightD) -> str:
    L = lambda ko, en: _L(r.lang, ko, en)  # noqa: E731
    o = [f"# {r.header[L('제목', 'Title')]}", ""]
    o.append(_t([(k, v) for k, v in r.header.items() if k != L("제목", "Title")], [L("항목", "Item"), L("내용", "Detail")]))
    o.append(_t([[L("상태", "Status")] + [x[1] for x in r.status], [L("완료/목표일", "Date")] + [x[2][5:] for x in r.status]],
                [""] + [x[0] for x in r.status]))
    o += [f"## D0 {L('증상 및 긴급 대응', 'Symptom and emergency response')}",
          f"- **{L('증상', 'Symptom')}**: {r.d0['symptom']}", f"- **ERA**: {r.d0['era']}", f"- {r.d0['why_8d']}", ""]
    o += [f"## D1 {L('팀 구성', 'Team')}", _t(r.d1, [L("기능", "Function"), L("역할", "Role")])]
    o += [f"## D2 {L('문제 기술', 'Problem description')}", f"**{L('문제 정의', 'Problem statement')}**: {r.d2['stmt']}", "",
          _t(r.d2["w5h2"], ["5W2H", L("내용", "Detail")]),
          f"**IS / IS NOT**", "", _t(r.d2["isnot"], ["", "IS", "IS NOT", L("시사점", "Therefore")])]
    if r.changes:
        o += [f"**{L('변경점(4M) 검토 · 발생 전 45일 ~ 후 10일', '4M changes · 45 days before to 10 days after')}**", "",
              _t(r.changes, ["No.", L("일자", "Date"), "4M", L("내용", "Change"),
                             L("원료 보정 후 단차", "Step after raw-material correction"), L("판정", "Verdict")])]
    o += [f"## D3 {L('봉쇄 조치', 'Containment')}",
          _t(r.d3["rows"], [L("위치", "Location"), L("수량", "Quantity"), L("조치", "Action"), L("담당", "Owner"), L("목표일", "Due")]),
          f"- **{L('유효성 확인', 'Effectiveness')}**: {r.d3['verify']}", f"- **{L('해제 기준', 'Exit criteria')}**: {r.d3['exit']}", ""]
    o += [f"## D4 {L('근본 원인', 'Root cause')}",
          _t([(L("발생 (Occurrence)", "Occurrence"), r.d4["occ_trc"], r.d4["occ_mrc"]),
              (L("미검출 (Non-detection)", "Non-detection"), r.d4["nd_trc"], r.d4["nd_mrc"])],
             ["", L("기술적 원인 (TRC)", "Technical root cause (TRC)"), L("시스템 원인 (MRC)", "Managerial root cause (MRC)")]),
          f"**5-Why · {L('발생', 'occurrence')}**", ""] + [f"{i}. {w}" for i, w in enumerate(r.d4["why_o"], 1)] + [
          "", f"**5-Why · {L('미검출', 'non-detection')}**", ""] + [f"{i}. {w}" for i, w in enumerate(r.d4["why_n"], 1)] + [
          "", f"**{L('6M 요인 검토', '6M screening')}**", "",
          _t(r.d4["m6"], ["6M", L("가설", "Hypothesis"), L("근거", "Evidence"), L("판정", "Verdict")]),
          f"**{L('원인 검증', 'Root cause verification')}**", ""] + [f"- {v}" for v in r.d4["verify"]] + [""]
    o += [f"## D5 {L('영구 시정조치 선정', 'Permanent corrective actions')}",
          _t(r.d5, [L("대상", "For"), L("조치", "Action"), L("검증 방법", "Verification"), L("담당", "Owner"), L("목표일", "Due")])]
    o += [f"## D6 {L('실행 및 유효성 확인', 'Implementation and validation')}",
          _t(r.d6["rows"], [L("대상", "For"), L("조치", "Action"), L("담당", "Owner"), L("목표일", "Due"), L("상태", "Status")]),
          f"**{L('유효성 판정 기준', 'Validation criteria')}**", ""] + [f"- {c}" for c in r.d6["criteria"]] + [""]
    o += [f"## D7 {L('재발 방지', 'Prevent recurrence')}",
          _t(r.d7["fmea"], [L("고장 모드", "Failure mode"), L("원인", "Cause"), L("관리 방법", "Control"), "O", "D"]),
          _t(r.d7["cp"], [L("관리 항목", "Characteristic"), L("측정 방법", "Method"), L("주기", "Frequency"), L("이상 시 조치", "Reaction")]),
          f"**{L('표준 개정', 'Standards')}**", ""] + [f"- {d}" for d in r.d7["docs"]] + [
          "", f"**{L('수평 전개', 'Horizontal deployment')}**", ""] + [f"- {d}" for d in r.d7["horizontal"]] + [
          "", f"**{L('교훈', 'Lesson learned')}**: {r.d7['lesson']}", ""]
    o += [f"## D8 {L('종결', 'Closure')}"] + [f"- [ ] {c}" for c in r.d8] + ["", f"_{r.notes[0]}_"]
    return "\n".join(o)


CSS = """
.doc,.doc *{font-size:12px!important}
.doc{font-family:'Malgun Gothic','Apple SD Gothic Neo',Pretendard,Inter,sans-serif;font-size:12px;color:#111;max-width:960px;margin:auto;background:#fff}
.doc h1{font-size:18px!important;font-weight:700!important;border-bottom:2px solid #111;padding-bottom:6px;margin:0 0 8px 0}
.doc h2{font-size:13px!important;font-weight:700!important;line-height:1.4!important;background:#1f2937;color:#fff;padding:4px 8px;margin:18px 0 6px 0;border-radius:2px}
.doc table{border-collapse:collapse;width:100%;margin:4px 0 10px 0}
.doc th,.doc td{border:1px solid #9ca3af;padding:4px 6px;vertical-align:top;text-align:left;font-size:12px!important;line-height:1.4}
.doc th{background:#e5e7eb;font-weight:600}
.doc p,.doc li{margin:2px 0;line-height:1.45;font-size:12px!important}
.doc ol,.doc ul{margin:2px 0 6px 18px;padding:0}
.doc em{color:#6b7280}
@page{size:A4;margin:14mm}
@media print{.doc{max-width:none}.doc h2{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def fragment(md: str) -> str:
    """Rendered 8D body (scoped CSS) for embedding in the app or another report."""
    import markdown_it
    body = markdown_it.MarkdownIt("commonmark").enable("table").render(md).replace("[ ]", "☐")
    return f"<style>{CSS}</style><div class='doc'>{body}</div>"


def html(md: str | EightD) -> str:
    """Stand-alone A4 page; print it to PDF from the browser."""
    if isinstance(md, EightD):
        md = markdown(md)
    return f"<!doctype html><html><head><meta charset='utf-8'><title>8D</title></head><body>{fragment(md)}</body></html>"


def xlsx(r: EightD) -> bytes:
    """Classic one-sheet Excel 8D form (what most plants still exchange)."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    L = lambda ko, en: _L(r.lang, ko, en)  # noqa: E731
    wb = Workbook()
    ws = wb.active
    ws.title = "8D"
    widths = [16, 34, 34, 30, 16, 12]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    thin = Side(style="thin", color="9CA3AF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")
    sec = PatternFill("solid", fgColor="1F2937")
    hd = PatternFill("solid", fgColor="E5E7EB")
    row = 1

    def line(vals, bold=False, fill=None, merge_last=False):
        nonlocal row
        for j, v in enumerate(vals):
            c = ws.cell(row=row, column=j + 1, value=v)
            c.alignment, c.border = wrap, box
            if bold:
                c.font = Font(bold=True, color="FFFFFF" if fill is sec else "111111")
            if fill:
                c.fill = fill
        if merge_last and len(vals) < 6:
            ws.merge_cells(start_row=row, start_column=len(vals), end_row=row, end_column=6)
        row += 1

    def section(title):
        nonlocal row
        line([title], bold=True, fill=sec, merge_last=True)

    ws.cell(row=1, column=1, value=r.header[L("제목", "Title")]).font = Font(bold=True, size=14)
    ws.merge_cells("A1:F1")
    row = 2
    for k, v in r.header.items():
        if k != L("제목", "Title"):
            line([k, v], merge_last=True)
    line([L("단계", "Step"), L("상태", "Status"), L("완료/목표일", "Date")], bold=True, fill=hd, merge_last=True)
    for s in r.status:
        line(list(s), merge_last=True)
    section(f"D0 {L('증상 및 긴급 대응', 'Symptom and emergency response')}")
    line([L("증상", "Symptom"), r.d0["symptom"]], merge_last=True)
    line(["ERA", r.d0["era"]], merge_last=True)
    section(f"D1 {L('팀 구성', 'Team')}")
    for f_, role in r.d1:
        line([f_, role], merge_last=True)
    section(f"D2 {L('문제 기술', 'Problem description')}")
    line([L("문제 정의", "Statement"), r.d2["stmt"]], merge_last=True)
    for k, v in r.d2["w5h2"]:
        line([k, v], merge_last=True)
    line(["", "IS", "IS NOT", L("시사점", "Therefore")], bold=True, fill=hd, merge_last=True)
    for v in r.d2["isnot"]:
        line(list(v), merge_last=True)
    if r.changes:
        line(["4M No.", L("일자 · 4M", "Date · 4M"), L("내용", "Change"), L("단차", "Step"), L("판정", "Verdict")], bold=True, fill=hd, merge_last=True)
        for c in r.changes:
            line([c[0], f"{c[1]} · {c[2]}", c[3], c[4], c[5]], merge_last=True)
    section(f"D3 {L('봉쇄 조치', 'Containment')}")
    line([L("위치", "Location"), L("수량", "Quantity"), L("조치", "Action"), L("담당", "Owner"), L("목표일", "Due")], bold=True, fill=hd, merge_last=True)
    for v in r.d3["rows"]:
        line(list(v), merge_last=True)
    line([L("해제 기준", "Exit"), r.d3["exit"]], merge_last=True)
    section(f"D4 {L('근본 원인', 'Root cause')}")
    line(["", "TRC", "MRC"], bold=True, fill=hd, merge_last=True)
    line([L("발생", "Occurrence"), r.d4["occ_trc"], r.d4["occ_mrc"]], merge_last=True)
    line([L("미검출", "Non-detection"), r.d4["nd_trc"], r.d4["nd_mrc"]], merge_last=True)
    for i, w in enumerate(r.d4["why_o"], 1):
        line([f"Why {i} ({L('발생', 'occ.')})", w], merge_last=True)
    for i, w in enumerate(r.d4["why_n"], 1):
        line([f"Why {i} ({L('미검출', 'det.')})", w], merge_last=True)
    line(["6M", L("가설", "Hypothesis"), L("근거", "Evidence"), L("판정", "Verdict")], bold=True, fill=hd, merge_last=True)
    for v in r.d4["m6"]:
        line(list(v), merge_last=True)
    for v in r.d4["verify"]:
        line([L("검증", "Verification"), v], merge_last=True)
    section(f"D5 / D6 {L('영구 시정조치 · 실행', 'Corrective actions · implementation')}")
    line([L("대상", "For"), L("조치", "Action"), L("검증 방법", "Verification"), L("담당", "Owner"), L("목표일", "Due"), L("상태", "Status")], bold=True, fill=hd)
    for v in r.d5:
        line(list(v) + [L("계획", "Planned")])
    for c in r.d6["criteria"]:
        line([L("유효성 기준", "Validation"), c], merge_last=True)
    section(f"D7 {L('재발 방지', 'Prevent recurrence')}")
    line([L("고장 모드", "Failure mode"), L("원인", "Cause"), L("관리 방법", "Control"), "O", "D"], bold=True, fill=hd, merge_last=True)
    for v in r.d7["fmea"]:
        line(list(v), merge_last=True)
    for d in r.d7["docs"] + r.d7["horizontal"]:
        line([L("표준/수평전개", "Standard/Deploy"), d], merge_last=True)
    line([L("교훈", "Lesson"), r.d7["lesson"]], merge_last=True)
    section(f"D8 {L('종결', 'Closure')}")
    for c in r.d8:
        line(["☐", c], merge_last=True)
    line(["", r.notes[0]], merge_last=True)
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
