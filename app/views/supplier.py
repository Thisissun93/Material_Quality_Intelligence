import plotly.graph_objects as go
import streamlit as st

import ui
from mqi import config
from mqi.analytics.supplier import paired
from mqi.insight import drafts


def render():
    st.title(ui.t("nav_supplier"))
    if not ui.has_data() or ui.bundle().score is None:
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    st.subheader(ui.t("scorecard"))
    st.dataframe(ui.localize(b.score), hide_index=True, width="stretch",
                 column_config={k: st.column_config.NumberColumn(format="%.2f") for k in ("min_ppk", "최저 Ppk", "Min Ppk")})

    st.subheader(ui.t("trust"))
    tr = b.trust.assign(_o=b.trust["trust"].map({"untrusted": 0, "verify": 1, "trusted": 2})).sort_values("_o")
    tr = ui.localize(tr.drop(columns="_o"))
    fmt = {"bias": "%+.3g", "평균 차이(CoA−자사)": "%+.3g", "Bias (CoA−in-house)": "%+.3g",
           "Bias / ½spec %": "%.1f", "Corr": "%.2f", "bias_pct_of_halfspec": "%.1f", "차이/½규격 %": "%.1f",
           "p_value": "%.3f", "p": "%.3f", "corr": "%.2f", "상관": "%.2f", "loa": "%.3g", "±LoA": "%.3g"}
    st.dataframe(tr, hide_index=True, width="stretch",
                 column_config={k: st.column_config.NumberColumn(format=v) for k, v in fmt.items() if k in tr})

    st.subheader(ui.t("coa_vs"))
    order = b.trust.assign(_o=b.trust["trust"].map({"untrusted": 0, "verify": 1, "trusted": 2})).sort_values("_o")
    opts = order[["supplier", "property"]].apply(tuple, axis=1).tolist()
    sup, prop = st.selectbox(" ", opts, format_func=lambda o: f"{o[0]} · {ui.prop_label(o[1])}",
                             label_visibility="collapsed")
    w = paired(b.raw)
    w = w[(w["supplier"] == sup) & (w["property"] == prop)]
    spec = config.all_property_specs()[prop]
    fig = ui.fig_base(320, yaxis_title=spec["unit"])
    fig.add_trace(go.Scatter(x=w["lot_id"], y=w["coa"], mode="lines+markers", name="CoA",
                             line=dict(color=ui.AMBER, width=2)))
    fig.add_trace(go.Scatter(x=w["lot_id"], y=w["inhouse"], mode="lines+markers",
                             name="In-house" if ui.lang() == "en" else "자사 측정", line=dict(color=ui.BLUE, width=2)))
    ui.spec_lines(fig, spec)
    st.plotly_chart(fig, width="stretch")

    with st.expander(ui.t("scar"), expanded=False):
        lang = st.segmented_control(" ", ["en", "ko"], default="en", key="scar_lang", label_visibility="collapsed")
        text = drafts.scar(b, sup, prop, lang or "en")
        st.markdown(text)
        st.download_button(ui.t("download"), text, file_name=f"SCAR_{sup}_{prop}.md")
