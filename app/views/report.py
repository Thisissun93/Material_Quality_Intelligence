import streamlit as st

import ui
from mqi import report
from mqi.insight import drafts, llm


def render():
    st.title(ui.t("nav_report"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    lang = ui.lang()
    c1, c2 = st.columns([1, 3])
    props = [p for p in b.spc if b.spc[p]["first"]["EWMA"] is not None] or list(b.spc)
    prop = c1.selectbox(ui.t("property"), props, format_func=ui.prop_label)
    html = report.build(b, lang)
    c1.download_button(ui.t("report_html"), html, file_name="mqi_report.html", mime="text/html",
                       type="primary", width="stretch")

    text = drafts.eight_d(b, prop, lang)
    if c1.button(ui.t("polish"), width="stretch", disabled=not llm.available()):
        with st.spinner("…"):
            text = llm.polish(text, lang)
    if not llm.available():
        c1.caption(ui.t("polish_off"))
    with c2:
        st.subheader(ui.t("eightd"))
        view, edit = st.tabs(["미리보기" if lang == "ko" else "Preview", "편집" if lang == "ko" else "Edit"])
        with edit:
            edited = st.text_area(" ", text, height=520, label_visibility="collapsed", key=f"8d_{prop}_{lang}")
        with view:
            st.markdown(edited)
        st.download_button(ui.t("download"), edited, file_name=f"8D_{prop}.md")
