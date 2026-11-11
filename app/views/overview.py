import numpy as np
import plotly.graph_objects as go
import streamlit as st

import ui
from mqi import config, engine
from mqi.insight import rules


def render():
    st.title(ui.t("nav_overview"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    h = engine.health(b)
    c = st.columns(6)
    ui.kpi(c[0], ui.t("lots"), h["lots"])
    wp = h["worst_ppk"]
    ui.kpi(c[1], ui.t("worst_ppk"), f"{wp:.2f}" if wp else "-", ui.prop_label(h["worst_prop"]) if wp else None,
           "bad" if wp and wp < 1 else "warn" if wp and wp < 1.33 else "")
    ui.kpi(c[2], ui.t("early"), h["spc_alarms"], tone="warn" if h["spc_alarms"] else "")
    ui.kpi(c[3], ui.t("hidden"), h["t2_hidden"], tone="warn" if h["t2_hidden"] else "")
    ui.kpi(c[4], ui.t("untrusted"), h["untrusted"], tone="bad" if h["untrusted"] else "")
    ui.kpi(c[5], ui.t("grr"), f"{h['grr']:.1f}%" if h["grr"] else "-",
           tone="warn" if h["grr"] and h["grr"] >= 10 else "")

    left, right = st.columns([3, 2], gap="large")
    with right:
        st.subheader(ui.t("findings"))
        lang = ui.lang()
        for f in rules.findings(b):
            st.markdown(
                f'<div class="card">{ui.chip(f.severity)} <span class="muted">{f.area}</span>'
                f'<div class="t">{f.t("title", lang)}</div>'
                f'<div class="e">{f.t("evidence", lang)}</div>'
                f'<div class="a">→ {f.t("action", lang)}</div></div>', unsafe_allow_html=True)
    with left:
        spec = config.specs()["product"]["properties"]
        for p in ("viscosity", "tg"):
            s = b.spc.get(p)
            if not s:
                continue
            x = b.product[p]
            fig = ui.fig_base(260, title=dict(text=f"{ui.prop_label(p)} ({spec[p]['unit']})", font_size=14))
            fig.add_trace(go.Scatter(x=ui.lot_axis(b), y=x, mode="lines+markers", name=ui.prop_label(p),
                                     line=dict(color=ui.MUTED, width=1), marker=dict(size=4, color=ui.INK)))
            fig.add_trace(go.Scatter(x=ui.lot_axis(b), y=s["ewma"]["ewma"], name="EWMA",
                                     line=dict(color=ui.BLUE, width=2)))
            al = s["ewma"]["alarm"].values & (np.arange(len(x)) >= b.baseline)
            fig.add_trace(go.Scatter(x=ui.lot_axis(b)[al], y=s["ewma"]["ewma"][al], mode="markers", name="alarm",
                                     marker=dict(color=ui.RED, size=5)))
            ui.spec_lines(fig, spec[p])
            fig.add_vrect(x0=ui.lot_axis(b).iloc[0], x1=ui.lot_axis(b).iloc[b.baseline - 1],
                          fillcolor="#f3f4f6", line_width=0, layer="below",
                          annotation_text=ui.t("baseline"), annotation_position="bottom left",
                          annotation_font_color=ui.MUTED)
            fig.update_xaxes(showticklabels=False)
            st.plotly_chart(fig, width="stretch")
        if b.score is not None:
            st.dataframe(ui.localize(b.score[["supplier", "material", "min_ppk", "coa_trust", "inspection"]]),
                         hide_index=True, width="stretch",
                         column_config={k: st.column_config.NumberColumn(format="%.2f") for k in ("min_ppk", "최저 Ppk", "Min Ppk")})
