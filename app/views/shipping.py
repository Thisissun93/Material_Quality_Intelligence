import pathlib
import tempfile

import pandas as pd
import streamlit as st

import ui
from mqi import config, oos, outgoing
from mqi.ingest import coa_docs

TONE = {"release": "🟢", "hold": "🟡", "block": "🔴"}


def render():
    ko = ui.lang() == "ko"
    st.title(ui.t("nav_ship"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    stat = outgoing.batch_status(b, 40)
    word = ({"release": "출하 가능", "hold": "보류 (QA 판단)", "block": "출하 불가"} if ko else
            {"release": "Release", "hold": "Hold (QA decision)", "block": "Block"})
    c = st.columns(3)
    for col, k in zip(c, ("release", "hold", "block")):
        ui.kpi(col, f"{TONE[k]} {word[k]}", int((stat['status'] == k).sum()),
               tone={"hold": "warn", "block": "bad"}.get(k, ""))

    left, right = st.columns([1, 1.3], gap="large")
    with left:
        show = stat.assign(s=stat["status"].map(lambda v: f"{TONE[v]} {word[v]}"),
                           why=stat["reasons"].map(lambda r: " · ".join(x[0 if ko else 1] for x in r)))[["lot_id", "s", "why"]]
        show.columns = ["Lot", "판정" if ko else "Status", "사유" if ko else "Reason"]
        ev = st.dataframe(show, hide_index=True, width="stretch", height=520, on_select="rerun",
                          selection_mode="single-row", key="ship_pick")
        rows = ev.selection.rows if ev and ev.selection else []
        lot = stat.iloc[rows[0]]["lot_id"] if rows else stat.iloc[0]["lot_id"]
    with right:
        lvl, why = outgoing.release_check(b, lot)
        st.markdown(f"**{lot}** · {TONE[lvl]} {word[lvl]}")
        for w in why:
            st.caption(w[0 if ko else 1])
        r = b.product[b.product["lot_id"] == lot].iloc[0]
        sp = config.specs()["product"]["properties"]
        tbl = pd.DataFrame([{("항목" if ko else "Item"): ui.prop_label(p), ("결과" if ko else "Result"): r[p],
                             ("규격" if ko else "Spec"): f"{s.get('lsl')}–{s.get('usl')} {s['unit']}"} for p, s in sp.items()])
        st.dataframe(tbl, hide_index=True, width="stretch",
                     column_config={tbl.columns[1]: st.column_config.NumberColumn(format="%.4g")})
        _history(lot, ko, any(oos_kw in w[1] for w in why for oos_kw in ("OOS", "repeat")))
        c1, c2, c3 = st.columns([2, 1, 1])
        cust = c1.text_input("고객사" if ko else "Customer", value="Customer A (demo)")
        qty = c2.number_input("수량 kg" if ko else "Qty kg", min_value=0.0, value=200.0, step=20.0)
        po = c3.text_input("PO No.")
        if lvl == "block":
            st.error("규격 이탈 또는 미측정 항목이 있어 성적서를 발행할 수 없습니다." if ko else
                     "Out of spec or missing results: no certificate can be issued.")
            return
        ok = lvl == "release" or st.checkbox(
            "QA 검토 완료, 정식 발행 승인" if ko else "Reviewed by QA, approve formal issue", key=f"ok_{lot}")
        data = outgoing.pdf(b, lot, cust, qty, po, draft=not ok)
        st.download_button(("성적서 PDF 발행" if ok else "초안 PDF (HOLD 워터마크)") if ko else
                           ("Issue CoA PDF" if ok else "Draft PDF (HOLD watermark)"),
                           data, file_name=f"CoA_{lot}{'' if ok else '_DRAFT'}.pdf", mime="application/pdf",
                           type="primary" if ok else "secondary")
        tmp = pathlib.Path(tempfile.gettempdir()) / f"mqi_out_{lot}_{int(ok)}.pdf"
        tmp.write_bytes(data)
        coa_docs.pages.cache_clear()
        st.image(coa_docs.pages(str(tmp), 1.3)[0], width="stretch")


def _done(msg: str):
    ui.clear()
    st.toast(msg)
    st.rerun()


def _history(lot: str, ko: bool, expanded: bool):
    """Every test result of the lot, OOS investigation (phase 1 / 2) and retest entry."""
    L = (lambda k, e: k) if ko else (lambda k, e: e)
    h = oos.history(lot)
    if len(h):
        order = {k: i for i, k in enumerate(config.specs()["product"]["properties"])}
        h = h.assign(_o=h["property"].map(order)).sort_values(["_o", "seq"]).drop(columns="_o")
    with st.expander(L("검사 이력 · OOS 조사 · 재시험", "Test history · OOS investigation · retest"), expanded=expanded):
        if h.empty:
            st.caption(L("측정 결과 없음", "No results"))
            return
        names = oos.ATTEMPT_KO if ko else oos.ATTEMPT_EN
        short = ({"initial": "최초", "remeasure": "재측정", "resample": "재채취", None: "사유 없음"} if ko else
                 {"initial": "Initial", "remeasure": "Re-measure", "resample": "Re-sample", None: "No reason"})
        show = pd.DataFrame({
            L("항목", "Item"): h["property"].map(ui.prop_label),
            L("차수", "Test"): h["seq"],
            L("구분", "Type"): h["attempt"].map(lambda a: short.get(a, a)),
            L("결과", "Result"): h["value"],
            L("판정", "Judge"): h["oos"].map(lambda x: L("이탈", "OOS") if x else L("합격", "Pass")),
            L("상태", "Status"): [L("무효", "Invalid") if i else L("유효", "Valid")
                                  for i in h["invalidated"]],
            "OOS": h["oos_no"].fillna(""),
            L("측정자", "Analyst"): h["operator"].fillna(""),
            L("일시", "When"): h["measured_at"].str[:16].str.replace("T", " "),
            L("재시험 사유", "Reason"): h["reason"].fillna(""),
        })
        st.dataframe(show, hide_index=True, width="stretch",
                     column_config={show.columns[3]: st.column_config.NumberColumn(format="%.4g")})
        st.caption(L("측정값은 수정·삭제하지 않습니다. 재시험은 새 행으로 추가되고, 최초 이탈 결과는 1단계 조사에서 "
                     "실험실 오류가 입증될 때만 무효가 됩니다.",
                     "Results are never edited or deleted. A retest is added as a new row; a failing result is set "
                     "aside only when phase 1 proves a laboratory error."))

        # 1) failing results that nobody is investigating yet
        for prop in h.loc[h["oos"], "property"].unique():
            u = oos.uninvestigated(lot, prop)
            if u.empty:
                continue
            r = u.iloc[0]
            with st.form(f"open_{lot}_{prop}"):
                st.warning(L(f"{ui.prop_label(prop)} {r['seq']}차 결과 {r['value']:.4g} 규격 이탈: OOS 조사를 등록해야 "
                             "재시험할 수 있습니다.",
                             f"{ui.prop_label(prop)} test {r['seq']} = {r['value']:.4g} is OOS: open an investigation "
                             "before any retest."))
                by = st.text_input(L("조사 담당 (QC)", "Investigator (QC)"), key=f"ob_{lot}_{prop}")
                if st.form_submit_button(L("OOS 조사 등록", "Open OOS investigation"), type="primary"):
                    try:
                        _done(oos.open_investigation(lot, prop, by))
                    except ValueError as e:
                        st.error(str(e))

        # 2) open investigations of this lot
        inv = oos.investigations(open_only=True)
        for _, i in inv[inv["lot_id"] == lot].iterrows():
            head = f"**{i['no']}** · {ui.prop_label(i['property'])} {i['value']:.4g} · {L('등록', 'opened')} " \
                   f"{str(i['opened'])[:10]} ({i['opened_by']})"
            with st.form(f"inv_{i['no']}"):
                st.markdown(head)
                if i["phase1"] == "pending":
                    st.caption(L("1단계 실험실 조사: 장비·측정자·시료 준비·계산·표준물질을 확인합니다. 근거가 있는 "
                                 "실험실 오류만 최초 결과를 무효로 할 수 있습니다.",
                                 "Phase 1 (laboratory): instrument, analyst, sample prep, calculation, standards. "
                                 "Only a documented lab error can invalidate the result."))
                    err = st.radio(L("실험실 오류", "Lab error"), [False, True], horizontal=True,
                                   format_func=lambda x: L("입증됨", "Proven") if x else L("없음", "Not found"))
                    cat = st.selectbox(L("오류 유형 (입증된 경우)", "Category (if proven)"), oos.LAB_CAUSES,
                                       format_func=lambda c: oos.LAB_CAUSE_KO[c] if ko else c)
                    cause = st.text_area(L("조사 근거", "Evidence"), height=70)
                    by = st.text_input(L("조사자", "Investigator"))
                    if st.form_submit_button(L("1단계 종결", "Close phase 1"), type="primary"):
                        try:
                            oos.record_phase1(i["no"], err, cause, by, cat if err else None)
                            _done(i["no"])
                        except ValueError as e:
                            st.error(str(e))
                else:
                    st.caption(L("실험실 오류가 확인되지 않아 최초 이탈이 유효합니다. 2단계 제조 조사 후 처리 방법을 "
                                 "정합니다. 재시험 합격과 관계없이 이 로트는 출하할 수 없습니다.",
                                 "No lab error: the OOS stands. Phase 2 (manufacturing) decides the disposition; "
                                 "the lot cannot ship whatever the retest shows."))
                    finding = st.text_area(L("제조 조사 결과", "Manufacturing finding"), height=70)
                    disp = st.selectbox(L("처리", "Disposition"), oos.DISPOSITIONS,
                                        format_func=lambda d: oos.DISPOSITION_KO[d] if ko else d)
                    by = st.text_input(L("담당 (생산·QA)", "Owner (production / QA)"))
                    if st.form_submit_button(L("OOS 확정", "Confirm OOS"), type="primary"):
                        try:
                            oos.record_phase2(i["no"], finding, disp, by)
                            _done(i["no"])
                        except ValueError as e:
                            st.error(str(e))

        # 3) retest entry
        with st.form(f"retest_{lot}"):
            st.markdown(L("**재시험 등록**", "**Add a retest**"))
            a, b_, c = st.columns([1.2, 1.2, 1])
            prop = a.selectbox(L("항목", "Item"), list(config.specs()["product"]["properties"]), format_func=ui.prop_label)
            kind = b_.selectbox(L("구분", "Type"), oos.ATTEMPTS, format_func=lambda k: names[k])
            val = c.number_input(L("결과", "Result"), value=None, format="%.4g")
            reason = st.text_input(L("재시험 사유", "Reason"))
            op = st.text_input(L("측정자", "Analyst"))
            if st.form_submit_button(L("재시험 저장", "Save retest")):
                try:
                    if val is None:
                        raise ValueError(L("결과를 입력하세요", "Enter a result"))
                    oos.add_retest(lot, prop, val, kind, reason, op)
                    _done(L("재시험 저장", "Retest saved"))
                except ValueError as e:
                    st.error(str(e))
