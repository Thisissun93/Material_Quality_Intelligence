import pathlib
import tempfile

import pandas as pd
import streamlit as st

import ui
from mqi import config, outgoing
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
