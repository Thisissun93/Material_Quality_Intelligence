"""streamlit run app/main.py"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Streamlit only hot-reloads modules inside app/. After a new deploy the long-running server would keep
# the old mqi package (old schema, old texts), so drop it whenever any source file changed.
_SRC = Path(__file__).resolve().parents[1]
_SIG = max(p.stat().st_mtime for d in ("mqi", "app") for p in (_SRC / d).rglob("*.py"))
if getattr(sys, "_mqi_sig", None) != _SIG:
    for _m in [m for m in sys.modules if m.split(".")[0] in ("mqi", "views", "ui")]:
        del sys.modules[_m]
    st.cache_data.clear()
    st.cache_resource.clear()
    sys._mqi_sig = _SIG

import ui  # noqa: E402
from views import actions, capability, intake, msa, overview, release, report, settings, shipping, spc, supplier

st.set_page_config(page_title="Material Quality Intelligence", page_icon="◆", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

with st.sidebar:
    st.session_state["lang"] = "ko" if st.segmented_control(
        "lang", ["한국어", "English"], default="한국어", label_visibility="collapsed") != "English" else "en"
    st.markdown(f"**{ui.t('app')}**")

P = {
    "overview": st.Page(overview.render, title=ui.t("nav_overview"), icon=":material/dashboard:", default=True),
    "report": st.Page(report.render, title=ui.t("nav_report"), icon=":material/description:", url_path="report"),
}
st.session_state["_pages"] = P
pages = {
    ui.t("group_data"): [
        P["overview"],
        # old links to /overview land on the same page instead of "Page not found"
        st.Page(overview.render, title=ui.t("nav_overview"), url_path="overview", visibility="hidden"),
        st.Page(intake.render, title=ui.t("nav_intake"), icon=":material/input:", url_path="intake"),
    ],
    ui.t("group_analysis"): [
        st.Page(msa.render, title=ui.t("nav_msa"), icon=":material/straighten:", url_path="msa"),
        st.Page(capability.render, title=ui.t("nav_cap"), icon=":material/bar_chart:", url_path="capability"),
        st.Page(spc.render, title=ui.t("nav_spc"), icon=":material/show_chart:", url_path="spc"),
        st.Page(supplier.render, title=ui.t("nav_supplier"), icon=":material/local_shipping:", url_path="supplier"),
        st.Page(release.render, title=ui.t("nav_release"), icon=":material/science:", url_path="release"),
    ],
    ui.t("group_action"): [
        P["report"],
        st.Page(actions.render, title=ui.t("nav_actions"), icon=":material/task_alt:", url_path="actions"),
        st.Page(shipping.render, title=ui.t("nav_ship"), icon=":material/verified:", url_path="shipping"),
        st.Page(settings.render, title=ui.t("nav_settings"), icon=":material/tune:", url_path="settings"),
    ],
}
nav = st.navigation(pages)

# first visit (e.g. on Streamlit Cloud): build the demo plant so every page has data
# also after an update that changed the demo scenarios
from mqi import demo as _demo  # noqa: E402
if not ui.config.DB_PATH.exists() or _demo.is_stale():
    with st.spinner("Building demo data…"):
        _demo.build()
    ui.clear()

with st.sidebar:
    st.divider()
    if st.button(ui.t("demo"), width="stretch"):
        from mqi import demo
        with st.spinner("…"):
            res = demo.build()
        ui.clear()
        st.toast(ui.t("demo_done", n=sum(r.status == "stored" for r in res)))
        st.rerun()

nav.run()
