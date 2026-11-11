"""Turn numbers into prioritised findings with evidence and the next action (KO / EN).

Thresholds live in config/rules.yaml. Each finding is traceable to the analysis that raised it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import config, engine

LABEL = {
    "viscosity": ("점도", "viscosity"), "tg": ("Tg", "Tg"), "peak_temp": ("경화 피크온도", "cure peak"),
    "enthalpy": ("반응열", "cure enthalpy"), "filler_pct": ("필러 함량", "filler content"),
    "d50": ("D50 입도", "D50"), "moisture": ("수분", "moisture"), "eew": ("에폭시 당량", "EEW"),
    "resin_visc": ("수지 점도", "resin viscosity"), "amine_value": ("아민가", "amine value"),
}
SEV = {"high": 0, "medium": 1, "low": 2}


def num(v: float) -> str:
    """Readable number: 11,862 / 3.05 / 0.0385."""
    if v is None or not np.isfinite(v):
        return "-"
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else f"{v:.2f}" if a >= 1 else f"{v:.3g}"


def snum(v: float) -> str:
    return ("+" if v >= 0 else "") + num(v)


def lab(p: str, lang: str) -> str:
    ko, en = LABEL.get(p, (p, p))
    return ko if lang == "ko" else en


@dataclass
class Finding:
    key: str
    area: str
    severity: str
    title: dict
    evidence: dict
    action: dict

    def t(self, field: str, lang: str) -> str:
        return getattr(self, field)[lang]


def _f(key, area, sev, title_ko, title_en, ev_ko, ev_en, act_ko, act_en) -> Finding:
    return Finding(key, area, sev, {"ko": title_ko, "en": title_en}, {"ko": ev_ko, "en": ev_en},
                   {"ko": act_ko, "en": act_en})


def findings(b: engine.Bundle) -> list[Finding]:
    r = config.rules()
    out: list[Finding] = []
    prod = b.product

    # ---- early warning on a product property, with root cause through genealogy
    for p, s in b.spc.items():
        f = s["first"]
        # a sustained shift must be confirmed by EWMA or CUSUM; Nelson patterns alone are too chatty
        conf = [v for v in (f["EWMA"], f["CUSUM"]) if v is not None]
        if not conf:
            continue
        confirmed = min(conf)
        early = (("Nelson", f["Nelson 2-8"]) if f["Nelson 2-8"] is not None and f["Nelson 2-8"] <= confirmed
                 else ("EWMA" if f["EWMA"] == confirmed else "CUSUM", confirmed))
        lot = prod["lot_id"].iloc[early[1]]
        three = f.get("3σ (rule 1)")
        lead = (three - early[1]) if three is not None else None
        lead_ko = f", 3σ 이탈보다 {lead}로트 먼저" if lead and lead > 0 else ""
        lead_en = f", {lead} lots before the 3σ limit" if lead and lead > 0 else ""
        cause_ko = cause_en = ""
        att = b.attribution.get(p)
        sd = prod[p].iloc[:b.baseline].std()
        if att is not None and len(att) and abs(att.iloc[0]["effect"]) >= 0.5 * sd:
            top = att.iloc[0]
            share = engine.supplier_share(b, p)
            sup = ""
            if len(share) and top["feature"] in ("d50", "moisture"):
                diff = (share["alarm"] - share["baseline"]).sort_values(ascending=False)
                sup = diff.index[0] if diff.iloc[0] > 0.05 else ""
            cause_ko = (f" 원인 후보: 원료 {lab(top['feature'], 'ko')} {num(top['baseline'])}→{num(top['alarm'])}"
                        f" (예측 영향 {snum(top['effect'])})" + (f", {sup} 로트 비중 증가" if sup else ""))
            cause_en = (f" Likely driver: raw {lab(top['feature'], 'en')} {num(top['baseline'])}→{num(top['alarm'])}"
                        f" (predicted effect {snum(top['effect'])})" + (f", more {sup} lots" if sup else ""))
        else:
            sh = engine.instrument_shift(p, b.dbpath, b=b)
            if sh and sh["p"] < 0.01 and abs(sh["diff"]) > 0.5 * sh["sd"]:
                o, nw = sh["old"].split(" · ")[0], sh["new"].split(" · ")[0]
                cause_ko = (f" 원료로는 설명되지 않음. 측정 장비 교체({o} → {nw}, {sh['since']}) 이후 "
                            f"평균 {snum(sh['diff'])} 차이 → 장비 간 상관성(Bias) 평가 필요")
                cause_en = (f" Not explained by raw materials. Instrument change ({o} → {nw}, "
                            f"{sh['since']}) moved the mean by {snum(sh['diff'])} → run an instrument correlation (bias) study")
                lot, lead_ko, lead_en = sh["first_lot"], "", ""
                early = ("ANCOVA", early[1])
            elif att is not None:
                cause_ko = " 원료 변화로는 설명되지 않음 → 공정 조건·측정 쪽 확인"
                cause_en = " Raw materials do not explain it → check process settings and measurement"
        instrument = early[0] == "ANCOVA"
        cap = b.cap.get(p)
        sev = "high" if ((cause_ko.startswith(" 원인") or "측정 장비" in cause_ko) and (three is not None or (cap and cap.ppk < r["capability"]["cpk_target"]))) else "medium"
        out.append(_f(
            f"spc_{p}", "SPC", sev,
            f"{lab(p, 'ko')} 추세 이동 감지 ({lot}부터)",
            f"{lab(p, 'en').capitalize()} shift detected (from {lot})",
            (f"장비 교체 첫 로트 {lot}." if early[0] == "ANCOVA" else f"최초 조기경보 {lot} ({early[0]}){lead_ko}.")
            + cause_ko,
            (f"First lot on the new instrument: {lot}." if early[0] == "ANCOVA"
             else f"First early warning at {lot} ({early[0]}){lead_en}.") + cause_en,
            "같은 보관 샘플 10개를 두 장비로 측정해 Bias·상관성 평가, 보정값 확정 전까지 출하 판정 보류"
            if instrument else "원인 원료 로트 격리·사용 순서 조정, 해당 공급사에 개선 요청(SCAR), 원료 수입검사 강화",
            "Measure 10 retained samples on both instruments (bias and correlation), hold release decisions until the "
            "offset is settled" if instrument else
            "Quarantine or re-sequence suspect raw lots, raise a SCAR to the supplier, tighten incoming inspection"))

    # ---- capability
    for p, c in b.cap.items():
        gap = c.cpk - c.ppk
        if c.ppk < r["capability"]["cpk_target"]:
            unstable = gap > r["capability"]["stability_gap"]
            out.append(_f(
                f"cap_{p}", "Capability", "high" if c.ppk < r["capability"]["cpk_min"] else "medium",
                f"{lab(p, 'ko')} 공정능력 부족 (Ppk {c.ppk:.2f})",
                f"{lab(p, 'en').capitalize()} capability below target (Ppk {c.ppk:.2f})",
                f"Cpk {c.cpk:.2f} vs Ppk {c.ppk:.2f}"
                + (" — 단기 산포는 작지만 평균이 이동 중(안정성 문제)" if unstable else " — 산포 자체가 큼"),
                f"Cpk {c.cpk:.2f} vs Ppk {c.ppk:.2f}"
                + (" — short-term spread is fine, the mean is moving (stability problem)" if unstable
                   else " — spread itself is too wide"),
                "산포 축소보다 평균 이동 원인(원료·장비) 제거가 우선" if unstable else "산포 요인(DOE)부터 분석",
                "Remove the source of the mean shift first (raw material / equipment)" if unstable
                else "Reduce variation first (DOE on key factors)"))

    # ---- hidden multivariate anomalies
    if b.t2 is not None:
        hidden = b.t2[b.t2["alarm"] & b.t2["univariate_ok"]]
        for i, row in hidden.iterrows():
            cc = row[[c for c in row.index if c.startswith("c_")]].sort_values(key=np.abs, ascending=False)
            top = [c[2:] for c in cc.index[:3]]
            lot = prod.loc[i, "lot_id"]
            out.append(_f(
                f"t2_{lot}", "Multivariate", "medium",
                f"{lot}: 단일 항목은 정상이지만 조합이 비정상 (T²)",
                f"{lot}: every value in limits, combination abnormal (T²)",
                f"T² {row['t2']:.1f} > UCL {row['ucl']:.1f}. 기여: " + ", ".join(lab(t, "ko") for t in top),
                f"T² {row['t2']:.1f} > UCL {row['ucl']:.1f}. Contributors: " + ", ".join(lab(t, "en") for t in top),
                "배합 기록(계량·투입 순서) 확인, 보관 샘플 재측정 후 출하 판정",
                "Check the batch record (weighing, addition order), re-test the retained sample before release"))

    # ---- measurement system
    g = b.gage
    if g:
        v = "acceptable" if g.grr_pct_sv < r["msa"]["grr_ok"] else (
            "marginal" if g.grr_pct_sv <= r["msa"]["grr_marginal"] else "unacceptable")
        if v != "acceptable" or g.ndc < r["msa"]["ndc_min"]:
            op = g.operator_bias.iloc[0]
            who_ko = f" {op['operator']} 측정값이 {op['offset']:+.0f} 치우침 (p={op['p_value']:.3f})" if op["p_value"] < 0.05 else ""
            who_en = f" {op['operator']} reads {op['offset']:+.0f} off (p={op['p_value']:.3f})" if op["p_value"] < 0.05 else ""
            repro = g.dominant == "reproducibility"
            out.append(_f(
                "msa", "MSA", "high" if v == "unacceptable" else "medium",
                f"점도계 측정시스템 {'조건부' if v == 'marginal' else '부적합'} (%GRR {g.grr_pct_sv:.1f}%)",
                f"Viscometer measurement system {v} (%GRR {g.grr_pct_sv:.1f}%)",
                f"{'재현성(측정자)' if repro else '반복성(장비)'} 비중이 큼, ndc {g.ndc}.{who_ko}",
                f"{'Reproducibility (operator)' if repro else 'Repeatability (equipment)'} dominates, ndc {g.ndc}.{who_en}",
                "측정 SOP(샘플 온도 안정화 시간·스핀들 침지 깊이) 재교육 후 재평가" if repro
                else "스핀들·온도조절 장치 점검, 교정 후 재평가",
                "Retrain on the SOP (temperature soak time, spindle immersion), then repeat the study" if repro
                else "Inspect spindle and temperature control, recalibrate, then repeat the study"))

    # ---- supplier CoA trust
    if b.trust is not None:
        for _, t in b.trust[b.trust["trust"] == "untrusted"].iterrows():
            since_ko = f", {t['drift_since']}부터 차이 확대" if t["drift_since"] else ""
            since_en = f", gap growing since {t['drift_since']}" if t["drift_since"] else ""
            out.append(_f(
                f"coa_{t['supplier']}_{t['property']}", "Supplier", "high",
                f"{t['supplier']} CoA {lab(t['property'], 'ko')} 신뢰 불가",
                f"{t['supplier']} CoA {lab(t['property'], 'en')} cannot be trusted",
                f"CoA−자사 평균 차이 {snum(t['bias'])} (p={t['p_value']:.3f}){since_ko}",
                f"Mean CoA − in-house {snum(t['bias'])} (p={t['p_value']:.3f}){since_en}",
                "CoA 기반 Skip-lot 중단 → 전수 수입검사, 공급사 측정법·교정 이력 요청",
                "Stop skip-lot on CoA, inspect every lot, request the supplier's method and calibration records"))
    if b.score is not None:
        for _, s in b.score[b.score["inspection"] == "tightened"].iterrows():
            if s["coa_trust"] == "untrusted":
                continue
            out.append(_f(
                f"sup_{s['supplier']}", "Supplier", "medium",
                f"{s['supplier']} 수입검사 강화 필요",
                f"{s['supplier']}: tighten incoming inspection",
                f"최저 Ppk {s['min_ppk']:.2f} ({lab(s['worst_property'], 'ko')}), 이동: {s['drifting']}",
                f"Lowest Ppk {s['min_ppk']:.2f} ({lab(s['worst_property'], 'en')}), drifting: {s['drifting']}",
                "공급사 공정능력 개선 계획 요청, 개선 전까지 전수 검사",
                "Ask for a capability improvement plan; inspect every lot until it improves"))
        for _, s_ in b.score[b.score["inspection"] == "pending"].iterrows():
            out.append(_f(
                f"pend_{s_['supplier']}", "Supplier", "low",
                f"{s_['supplier']} 판정 보류 (로트 {s_['lots']}개)",
                f"{s_['supplier']}: not enough lots to judge ({s_['lots']})",
                f"Ppk {s_['min_ppk']:.2f}, 95% CI {s_['ppk_ci']} ({lab(s_['worst_property'], 'ko')})",
                f"Ppk {s_['min_ppk']:.2f}, 95% CI {s_['ppk_ci']} ({lab(s_['worst_property'], 'en')})",
                "보통 검사 유지, 로트 누적 후 재판정. 평균이 상한 쪽이면 공급사에 중심값 조정 요청",
                "Keep normal inspection and re-judge with more lots; ask the supplier to centre the process"))

    # ---- release risk on what is in stock
    if b.matching is not None and len(b.matching):
        rk = r["release"]
        bad = b.matching[b.matching["risk"] > rk["risk_watch"]]
        if len(bad):
            worst = bad.iloc[-1]
            best = b.matching.iloc[0]
            out.append(_f(
                "release", "Release", "high" if (bad["risk"] > rk["risk_block"]).any() else "medium",
                f"재고 원료 조합 {len(bad)}건이 규격 이탈 위험",
                f"{len(bad)} raw-lot combinations in stock carry out-of-spec risk",
                f"최악: {worst['resin_lot']} + {worst['filler_lot']} → 예측 이탈확률 {worst['risk']:.0%}",
                f"Worst: {worst['resin_lot']} + {worst['filler_lot']} → predicted OOS probability {worst['risk']:.0%}",
                f"다음 배치는 {best['resin_lot']} + {best['filler_lot']} + {best['hardener_lot']} 조합 권장",
                f"Use {best['resin_lot']} + {best['filler_lot']} + {best['hardener_lot']} for the next batch"))

    out.sort(key=lambda f: (SEV[f.severity], f.area))
    # ---- open complaints and overdue actions
    from .. import qms
    cc = qms.complaints(b.dbpath)
    for _, c in cc[cc["status"] == "Open"].iterrows():
        out.append(_f(
            f"cc_{c['no']}", "Customer", "high",
            f"고객 클레임 {c['no']} ({c['customer']}) 진행 중", f"Open customer complaint {c['no']} ({c['customer']})",
            f"로트 {c['lot_id']} · {c['symptom']}", f"Lot {c['lot_id']} · {c['symptom']}",
            "보고서에서 클레임 기준 8D 작성, 24시간 내 봉쇄(D3) 회신", "Open the complaint 8D on Reports; containment reply within 24 h"))
    sm = qms.summary(b.dbpath)
    if sm["overdue"]:
        out.append(_f("act_overdue", "Actions", "medium", f"기한 지난 조치 {sm['overdue']}건",
                      f"{sm['overdue']} actions past due", f"진행 중 {sm['open']} / 전체 {sm['total']}",
                      f"{sm['open']} open of {sm['total']}", "조치·클레임 화면에서 담당·기한 재확인",
                      "Review owners and dates on the Actions page"))
    return out
