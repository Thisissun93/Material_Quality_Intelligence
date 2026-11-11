import pandas as pd
import streamlit as st

import ui
from mqi import config, qms


def render():
    ko = ui.lang() == "ko"
    st.title(ui.t("nav_actions"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    t1, t2 = st.tabs(["조치 현황" if ko else "Action register", "고객 클레임" if ko else "Customer complaints"])
    with t1:
        _actions(b, ko)
    with t2:
        _complaints(b, ko)


def _actions(b, ko):
    sm = qms.summary(b.dbpath)
    c = st.columns(4)
    ui.kpi(c[0], "전체" if ko else "Total", sm["total"])
    ui.kpi(c[1], "진행 중" if ko else "Open", sm["open"])
    ui.kpi(c[2], "기한 초과" if ko else "Overdue", sm["overdue"], tone="bad" if sm["overdue"] else "")
    ui.kpi(c[3], "완료" if ko else "Done", sm["done"], tone="good" if sm["done"] else "")
    a = qms.actions(dbpath=b.dbpath)
    if a.empty:
        st.caption("보고서 화면에서 8D를 만든 뒤 '조치 등록'을 누르면 여기에 쌓입니다." if ko else
                   "Create an 8D on the Reports page and press 'Register actions'.")
        return
    refs = sorted(a["ref"].unique())
    f1, f2 = st.columns([2, 1])
    pick = f1.multiselect("8D / SCAR", refs, placeholder="전체" if ko else "All")
    only = f2.toggle("미완료만" if ko else "Open only", value=False)
    v = a[a["ref"].isin(pick)] if pick else a
    if only:
        v = v[v["status"].isin(["Open", "In progress"])]
    v = v.assign(due=pd.to_datetime(v["due"], errors="coerce"), note=v["note"].fillna(""),
                 flag=v["overdue"].map({True: "⚠", False: ""}))
    cols = ["id", "flag", "ref", "step", "action", "owner", "due", "status", "note", "kind", "verification"]
    lab = (dict(flag="", ref="8D", step="단계", kind="구분", action="조치", verification="검증 방법", owner="담당",
                due="목표일", status="상태", note="메모") if ko else
           dict(flag="", ref="8D", step="Step", kind="Type", action="Action", verification="Verification",
                owner="Owner", due="Due", status="Status", note="Note"))
    ed = st.data_editor(
        v[cols], hide_index=True, width="stretch", key="act_ed", disabled=["id", "flag", "ref", "step", "kind", "action"],
        column_config={"id": None, "flag": st.column_config.TextColumn(lab["flag"], width=30),
                       "ref": st.column_config.TextColumn(lab["ref"], width=110),
                       "step": st.column_config.TextColumn(lab["step"], width=40),
                       "kind": st.column_config.TextColumn(lab["kind"], width="small"),
                       "action": st.column_config.TextColumn(lab["action"], width=360),
                       "verification": st.column_config.TextColumn(lab["verification"]),
                       "owner": st.column_config.TextColumn(lab["owner"], width=130),
                       "due": st.column_config.DateColumn(lab["due"], format="YYYY-MM-DD", width=105),
                       "status": st.column_config.SelectboxColumn(
                           lab["status"], options=qms.STATUS, required=True,
                           format_func=(lambda s: qms.STATUS_KO.get(s, s)) if ko else None),
                       "note": st.column_config.TextColumn(lab["note"])})
    if st.button("저장" if ko else "Save", type="primary"):
        ed = ed.assign(due=pd.to_datetime(ed["due"]).dt.strftime("%Y-%m-%d"))
        qms.update(ed, b.dbpath)
        st.toast("저장됨" if ko else "Saved")
        st.rerun()
    st.download_button("Excel", _xlsx(a), file_name="action_register.xlsx")


def _xlsx(a):
    from io import BytesIO
    buf = BytesIO()
    a.drop(columns=["overdue"]).to_excel(buf, index=False)
    return buf.getvalue()


def _complaints(b, ko):
    cc = qms.complaints(b.dbpath)
    with st.expander("새 클레임 등록" if ko else "Log a complaint", expanded=cc.empty):
        with st.form("cc_new", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cust = c1.text_input("고객사" if ko else "Customer")
            lot = c2.selectbox("로트" if ko else "Lot", b.product["lot_id"].tolist()[::-1])
            prop = c3.selectbox("관련 항목" if ko else "Property", list(config.specs()["product"]["properties"]),
                                format_func=ui.prop_label)
            sym = st.text_input("고객 표현 (증상)" if ko else "Customer's words (symptom)")
            c4, c5, c6 = st.columns(3)
            qty = c4.number_input("수량 (kg)" if ko else "Quantity (kg)", min_value=0.0, value=0.0)
            sev = c5.selectbox("심각도" if ko else "Severity", ["critical", "major", "minor"], index=1)
            rec = c6.date_input("접수일" if ko else "Received")
            if st.form_submit_button("등록" if ko else "Add", type="primary") and cust and sym:
                qms.add_complaint(cust, lot, prop, sym, qty, "kg", sev, rec.isoformat(), b.dbpath)
                st.rerun()
    if cc.empty:
        return
    show = cc[["no", "received", "customer", "lot_id", "property", "symptom", "qty", "severity", "status"]].copy()
    show["property"] = show["property"].map(ui.prop_label)
    show.columns = (["번호", "접수", "고객", "로트", "항목", "증상", "수량", "심각도", "상태"] if ko else
                    ["No.", "Received", "Customer", "Lot", "Property", "Symptom", "Qty", "Severity", "Status"])
    st.dataframe(show, hide_index=True, width="stretch")
    no = st.selectbox("8D 작성할 클레임" if ko else "Complaint for an 8D", cc["no"].tolist())
    if st.button("이 클레임으로 8D 작성" if ko else "Open 8D for this complaint", type="primary"):
        st.session_state["8d_source"] = no
        st.switch_page(st.session_state["_pages"]["report"])
