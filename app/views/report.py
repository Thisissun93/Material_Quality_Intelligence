import streamlit as st

import ui
from mqi import report
from mqi.insight import eightd, llm


def render():
    st.title(ui.t("nav_report"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    lang = ui.lang()
    ko = lang == "ko"
    c1, c2 = st.columns([1, 3.2])
    props = [p for p in b.spc if b.spc[p]["first"]["EWMA"] is not None] or list(b.spc)
    prop = c1.selectbox(ui.t("property"), props, format_func=ui.prop_label)

    r = eightd.build(b, prop, lang)
    base = eightd.markdown(r)
    key_txt = f"8d_{prop}_{lang}"
    if key_txt not in st.session_state:
        st.session_state[key_txt] = base

    with c1.expander(ui.t("ai_key"), expanded=False):
        st.text_input("API key", type="password", key="api_key", placeholder="sk-ant-…",
                      label_visibility="collapsed")
        st.caption(ui.t("ai_key_note"))
    key = st.session_state.get("api_key") or None
    if c1.button(ui.t("polish"), width="stretch", disabled=not llm.available(key)):
        try:
            with st.spinner("…"):
                st.session_state[key_txt] = llm.polish(st.session_state[key_txt], lang, key)
        except Exception as e:
            c1.error(f"API: {e}")
    if not llm.available(key):
        c1.caption(ui.t("polish_off"))
    if c1.button("초안으로 되돌리기" if ko else "Reset to draft", width="stretch"):
        st.session_state[key_txt] = base
        st.rerun()

    with c2:
        view, edit = st.tabs(["미리보기" if ko else "Preview", "편집" if ko else "Edit"])
        with edit:
            st.text_area(" ", key=key_txt, height=640, label_visibility="collapsed")
        text = st.session_state[key_txt]
        with view:
            st.html(eightd.fragment(text))

    c1.divider()
    c1.download_button("8D · HTML (A4, PDF 인쇄)" if ko else "8D · HTML (A4, print to PDF)",
                       eightd.html(text), file_name=f"8D_{prop}.html", mime="text/html",
                       type="primary", width="stretch")
    c1.download_button("8D · Excel", eightd.xlsx(r), file_name=f"8D_{prop}.xlsx", width="stretch",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help=("편집 전 초안 기준" if ko else "Built from the unedited draft"))
    c1.download_button("8D · Markdown", text, file_name=f"8D_{prop}.md", width="stretch")
    c1.download_button(ui.t("report_html"), report.build(b, lang), file_name="mqi_report.html",
                       mime="text/html", width="stretch")
