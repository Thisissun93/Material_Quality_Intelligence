"""Ready-to-edit documents built from the evidence: 8D report and supplier SCAR."""
from __future__ import annotations

from datetime import date

from .. import config, engine
from .rules import lab, num, snum


def eight_d(b: engine.Bundle, prop: str = "viscosity", lang: str = "ko") -> str:
    """8D in markdown. Kept for compatibility; the structure lives in eightd.py."""
    from . import eightd
    return eightd.markdown(eightd.build(b, prop, lang))


def scar(b: engine.Bundle, supplier: str, prop: str, lang: str = "en") -> str:
    raw = b.raw
    w = raw[(raw["supplier"] == supplier) & (raw["property"] == prop)]
    p = w.pivot_table(index=["lot_id", "made_at"], columns="source", values="value").reset_index()
    p = p.sort_values("made_at").tail(8)
    t = b.trust[(b.trust["supplier"] == supplier) & (b.trust["property"] == prop)]
    t = t.iloc[0] if len(t) else None
    unit = config.all_property_specs()[prop]["unit"]
    ko = lang == "ko"
    from datetime import date, timedelta
    mat = w["material"].iloc[0] if len(w) else ""
    today = date.today()
    L = [f"# SCAR — {supplier} · {lab(prop, lang)}\n",
         "| | |\n|---|---|",
         f"| {'SCAR 번호' if ko else 'SCAR No.'} | SCAR-{today:%y%m}-{supplier[:3].upper()}-{prop[:3].upper()} |",
         f"| {'공급사 / 자재' if ko else 'Supplier / material'} | {supplier} / {config.specs()['raw_materials'].get(mat, {}).get('label', mat)} |",
         f"| {'관련 로트' if ko else 'Lots concerned'} | {', '.join(p['lot_id'].tail(4))} |",
         f"| {'발행일' if ko else 'Issued'} | {today} |",
         f"| {'회신 기한' if ko else 'Response due'} | {'봉쇄(D3)' if ko else 'Containment (D3)'} {today + timedelta(days=2)} · "
         f"{'원인/대책(D4–D5)' if ko else 'Root cause & action (D4–D5)'} {today + timedelta(days=14)} · "
         f"{'유효성(D6)' if ko else 'Validation (D6)'} {today + timedelta(days=45)} |",
         f"| {'구분' if ko else 'Category'} | {'CoA-수입검사 불일치 (품질 문서 신뢰성)' if ko else 'CoA vs incoming inspection mismatch'} |\n"]
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
