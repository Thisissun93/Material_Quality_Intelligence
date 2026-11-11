"""Ready-to-edit documents built from the evidence: 8D report and supplier SCAR."""
from __future__ import annotations

from datetime import date

from .. import config, engine
from .rules import lab, num, snum


def eight_d(b: engine.Bundle, prop: str = "viscosity", lang: str = "ko") -> str:
    s = config.specs()["product"]
    spec = s["properties"][prop]
    sp = b.spc.get(prop)
    cap = b.cap.get(prop)
    i = next((v for k, v in (sp["first"].items() if sp else []) if k != "3σ (rule 1)" and v is not None), None)
    i = min([v for v in sp["first"].values() if v is not None], default=None) if sp else None
    lot = b.product["lot_id"].iloc[i] if i is not None else "-"
    att = b.attribution.get(prop)
    share = engine.supplier_share(b, prop)
    rows = att.head(4) if att is not None else None
    after = engine.lots_after(b, prop)
    x = b.product[prop]
    ko = lang == "ko"
    L = []
    L.append(f"# 8D — {s['name']} {lab(prop, lang)}" + (" 상승" if ko else " shift"))
    L.append(f"{date.today():%Y-%m-%d} · draft\n")
    L.append("## D2 " + ("문제 기술" if ko else "Problem description"))
    L.append((f"- 대상: {s['id']} {lab(prop, 'ko')} (규격 {spec.get('lsl')}–{spec.get('usl')} {spec['unit']})" if ko else
              f"- Item: {s['id']} {lab(prop, 'en')} (spec {spec.get('lsl')}–{spec.get('usl')} {spec['unit']})"))
    L.append((f"- 기준 구간 평균 {x.iloc[:b.baseline].mean():.0f} → 최근 10로트 평균 {x.tail(10).mean():.0f}" if ko else
              f"- Baseline mean {x.iloc[:b.baseline].mean():.0f} → last 10 lots {x.tail(10).mean():.0f}"))
    L.append((f"- 최초 조기경보: {lot}, 영향 로트 {len(after)}개" if ko else
              f"- First early alarm: {lot}; {len(after)} lots affected"))
    if cap:
        L.append(f"- Cpk {cap.cpk:.2f} / Ppk {cap.ppk:.2f}")
    L.append("\n## D3 " + ("임시 조치" if ko else "Containment"))
    L.append("- " + ("영향 로트 출하 전 재검 및 고객 통보 여부 판단" if ko else "Re-test affected lots before shipment; decide on customer notice"))
    L.append("- " + ("의심 원료 로트 격리, 다음 배치는 위험도 최저 조합 사용" if ko else "Quarantine suspect raw lots; use the lowest-risk combination next"))
    L.append("\n## D4 " + ("원인 분석 (데이터 근거)" if ko else "Root cause (data evidence)"))
    if rows is not None:
        L.append("| " + ("원료 인자" if ko else "Raw attribute") + " | Baseline | Alarm | "
                 + ("예측 영향" if ko else "Predicted effect") + " |")
        L.append("|---|---|---|---|")
        for _, r in rows.iterrows():
            L.append(f"| {lab(r['feature'], lang)} | {num(r['baseline'])} | {num(r['alarm'])} | {snum(r['effect'])} |")
    if len(share):
        L.append("")
        L.append(("필러 공급사 비중 (기준→경보): " if ko else "Filler supplier share (baseline→alarm): ")
                 + ", ".join(f"{k} {v.baseline:.0%}→{v.alarm:.0%}" for k, v in share.iterrows()))
    if b.trust is not None:
        bad = b.trust[b.trust["trust"] == "untrusted"]
        for _, t in bad.iterrows():
            L.append(("- " + f"{t['supplier']} CoA {lab(t['property'], 'ko')}가 자사 측정과 불일치 "
                      f"(차이 {snum(t['bias'])}, {t['drift_since'] or '-'}부터 확대) → CoA만으로 이상을 놓침") if ko else
                     ("- " + f"{t['supplier']} CoA {lab(t['property'], 'en')} disagrees with in-house data "
                      f"(gap {snum(t['bias'])}, growing since {t['drift_since'] or '-'}) → CoA alone missed it"))
    L.append("\n## D5/D6 " + ("영구 대책" if ko else "Permanent corrective action"))
    L.append("- " + ("공급사 SCAR 발행: 분급 공정 변경 여부, 측정법·교정 이력 확인" if ko else
                     "Issue SCAR: classification process change? measurement method and calibration records"))
    L.append("- " + ("필러 D50 수입검사 전수화, 관리한계 = 제품 영향 기준으로 재설정" if ko else
                     "100% incoming D50 check; set limits from the product-impact model"))
    L.append("\n## D7 " + ("재발 방지" if ko else "Prevent recurrence"))
    L.append("- " + ("EWMA 조기경보 + CoA 신뢰도 상시 모니터링, 배치 전 원료 조합 위험도 확인 의무화" if ko else
                     "Keep EWMA early warning and CoA trust monitoring; require a pre-batch risk check"))
    return "\n".join(L)


def scar(b: engine.Bundle, supplier: str, prop: str, lang: str = "en") -> str:
    raw = b.raw
    w = raw[(raw["supplier"] == supplier) & (raw["property"] == prop)]
    p = w.pivot_table(index=["lot_id", "made_at"], columns="source", values="value").reset_index()
    p = p.sort_values("made_at").tail(8)
    t = b.trust[(b.trust["supplier"] == supplier) & (b.trust["property"] == prop)]
    t = t.iloc[0] if len(t) else None
    unit = config.all_property_specs()[prop]["unit"]
    ko = lang == "ko"
    L = [f"**{'제목' if ko else 'Subject'}:** SCAR — {lab(prop, lang)} {'불일치' if ko else 'discrepancy'} ({supplier})\n"]
    if ko:
        L.append(f"{supplier} 품질 담당자님,\n\n최근 납품 로트에서 CoA의 {lab(prop, 'ko')} 값과 당사 수입검사 값의 차이가 커지고 있어 시정조치를 요청드립니다.")
    else:
        L.append(f"Dear {supplier} Quality team,\n\nThe {lab(prop, 'en')} on your CoA no longer matches our incoming inspection. "
                 "We request a corrective action.")
    L.append(f"\n| Lot | CoA ({unit}) | {'자사' if ko else 'In-house'} ({unit}) | Δ |\n|---|---|---|---|")
    for _, r in p.iterrows():
        c, h = r.get("coa", float("nan")), r.get("inhouse", float("nan"))
        L.append(f"| {r['lot_id']} | {num(c)} | {num(h)} | {snum(c - h)} |")
    if t is not None:
        L.append(("\n평균 차이 " if ko else "\nMean difference ") + f"{snum(t['bias'])} {unit}, p = {t['p_value']:.3f}"
                 + (f", {'확대 시작' if ko else 'growing since'} {t['drift_since']}" if t["drift_since"] else ""))
    L.append("\n" + ("요청 사항" if ko else "Please provide") + ":")
    items = (["측정 방법·장비·최근 교정 기록", "최근 공정 변경(분급·원료·설비) 여부", "원인 분석과 대책(8D) — 회신 기한 10영업일"]
             if ko else ["Test method, instrument and latest calibration record",
                         "Any recent process change (classification, raw material, equipment)",
                         "Root cause and corrective action (8D) within 10 working days"])
    L += [f"{i}. {x}" for i, x in enumerate(items, 1)]
    L.append("\n" + ("개선 확인 전까지 해당 자재는 전수 수입검사로 전환합니다." if ko else
                     "Until this is closed, every lot will be fully inspected on receipt."))
    return "\n".join(L)
