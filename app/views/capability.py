import numpy as np
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

import ui
from mqi import config
from mqi.analytics import capability as capmod


def render():
    st.title(ui.t("nav_cap"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    spec = config.specs()["product"]["properties"]
    props = list(b.cap)
    p = st.segmented_control(ui.t("property"), props, default=props[0], format_func=ui.prop_label,
                             label_visibility="collapsed") or props[0]
    c, s, x = b.cap[p], spec[p], b.product[p].values
    tgt = config.rules()["capability"]["cpk_target"]

    m = st.columns(6)
    tone = lambda v: "bad" if v < 1 else "warn" if v < tgt else ""  # noqa: E731
    ui.kpi(m[0], "Cpk", f"{c.cpk:.2f}", f"{ui.t('within')} σ {c.sd_within:.3g}", tone(c.cpk))
    ui.kpi(m[1], "Ppk", f"{c.ppk:.2f}", f"{ui.t('overall')} σ {c.sd_overall:.3g}", tone(c.ppk))
    ui.kpi(m[2], "Cpk 95% CI", f"{c.cpk_ci[0]:.2f} – {c.cpk_ci[1]:.2f}")
    ui.kpi(m[3], ui.t("ppm"), f"{c.ppm:,.0f}")
    ui.kpi(m[4], ui.t("normality"), f"{c.normal_p:.3f}", c.method)
    ui.kpi(m[5], "n", c.n)
    if c.cpk - c.ppk > config.rules()["capability"]["stability_gap"]:
        st.warning("Cpk ≫ Ppk → " + ("평균이 이동하는 불안정 공정. 공정능력 수치보다 SPC 원인 제거가 먼저예요."
                                     if ui.lang() == "ko" else
                                     "the mean is moving. Fix the special cause (SPC) before quoting capability."))

    left, right = st.columns([3, 2], gap="large")
    with left:
        fig = ui.fig_base(340)
        fig.add_trace(go.Histogram(x=x, nbinsx=24, marker_color="#cbd5e1", name="data", histnorm="probability density"))
        grid = np.linspace(min(x.min(), s.get("lsl") or x.min()), max(x.max(), s.get("usl") or x.max()), 300)
        fig.add_trace(go.Scatter(x=grid, y=stats.norm.pdf(grid, c.mean, c.sd_overall), name=ui.t("overall"),
                                 line=dict(color=ui.BLUE, width=2)))
        fig.add_trace(go.Scatter(x=grid, y=stats.norm.pdf(grid, c.mean, c.sd_within), name=ui.t("within"),
                                 line=dict(color=ui.AMBER, width=2, dash="dot")))
        ui.spec_lines(fig, s, axis="x")
        fig.update_layout(bargap=0.05, hovermode="closest", xaxis_title=s["unit"])
        st.plotly_chart(fig, width="stretch")
    with right:
        roll = capmod.rolling(x, s.get("lsl"), s.get("usl"), 25)
        fig = ui.fig_base(340, title=dict(text=ui.t("rolling"), font_size=13))
        fig.add_trace(go.Scatter(x=ui.lot_axis(b), y=roll, line=dict(color=ui.INK, width=2), name="Ppk"))
        fig.add_hline(tgt, line_dash="dash", line_color=ui.GREEN, annotation_text=f"{tgt}")
        fig.add_hline(1.0, line_dash="dash", line_color=ui.RED, annotation_text="1.0")
        fig.update_xaxes(showticklabels=False)
        st.plotly_chart(fig, width="stretch")
