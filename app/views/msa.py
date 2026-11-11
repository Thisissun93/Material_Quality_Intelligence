import plotly.graph_objects as go
import streamlit as st

import ui
from mqi import config
from mqi.analytics.msa import verdict

COLORS = [ui.BLUE, ui.AMBER, ui.GREEN, "#7c3aed", "#db2777"]


def render():
    st.title(ui.t("nav_msa"))
    b = ui.bundle() if ui.has_data() else None
    if not b or not b.gage:
        st.info(ui.t("empty"))
        return
    g, d = b.gage, b.gage_data
    r = config.rules()["msa"]
    v = verdict(g.grr_pct_sv, r["grr_ok"], r["grr_marginal"])
    sev = {"acceptable": "ok", "marginal": "medium", "unacceptable": "high"}[v]
    c = st.columns(5)
    tone = {"acceptable": "", "marginal": "warn", "unacceptable": "bad"}[v]
    ui.kpi(c[0], "%GRR (StudyVar)", f"{g.grr_pct_sv:.1f}%", tone=tone)
    ui.kpi(c[1], "%GRR (Tolerance)", f"{g.grr_pct_tol:.1f}%" if g.grr_pct_tol else "-")
    ui.kpi(c[2], "ndc", g.ndc, tone="" if g.ndc >= r["ndc_min"] else "bad")
    ui.kpi(c[3], ui.t("verdict"), ui.t(v), tone=tone)
    ui.kpi(c[4], "Dominant", g.dominant)
    st.markdown(f'<span class="chip {sev}">{ui.t(v)}</span> <span class="muted">'
                f'AIAG: &lt;{r["grr_ok"]}% · {r["grr_ok"]}–{r["grr_marginal"]}% · &gt;{r["grr_marginal"]}% · '
                f'ndc ≥ {r["ndc_min"]} · K = {g.k:g} · interaction {"kept" if g.interaction_kept else "pooled"}'
                f'</span>', unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        fig = ui.fig_base(330)
        parts = sorted(d["part"].unique(), key=lambda x: int(x) if str(x).isdigit() else x)
        for i, (op, gg) in enumerate(d.groupby("operator")):
            m = gg.groupby("part")["value"].mean().reindex(parts)
            fig.add_trace(go.Scatter(x=[str(p) for p in parts], y=m, mode="lines+markers", name=str(op),
                                     line=dict(color=COLORS[i % 5])))
        fig.update_layout(xaxis_title="Part", yaxis_title=ui.prop_label(d["property"].iloc[0]))
        st.plotly_chart(fig, width="stretch")
        comp = g.components.iloc[[1, 3, 4, 5]]
        bar = ui.fig_base(230)
        bar.add_trace(go.Bar(x=comp["Source"].str.strip(), y=comp["%StudyVar"], marker_color=ui.BLUE,
                             name="%StudyVar", text=comp["%StudyVar"].round(1), textposition="outside"))
        bar.update_yaxes(range=[0, 115], title="%StudyVar")
        bar.add_hline(r["grr_ok"], line_dash="dot", line_color=ui.AMBER)
        bar.add_hline(r["grr_marginal"], line_dash="dot", line_color=ui.RED)
        st.plotly_chart(bar, width="stretch")
    with right:
        cols = ["Source", "%Contribution", "%StudyVar"] + (["%Tolerance"] if "%Tolerance" in g.components else [])
        st.dataframe(g.components[cols], hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.1f") for c in cols[1:]})
        st.dataframe(g.anova.astype(object).where(g.anova.notna(), ""), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.4g")
                                    for c in g.anova.columns if c != "Source"})
        st.dataframe(g.operator_bias.rename(columns={"operator": ui.t("operator"), "offset": ui.t("offset")}),
                     hide_index=True, width="stretch",
                     column_config={"p_value": st.column_config.NumberColumn(format="%.4f"),
                                    ui.t("offset"): st.column_config.NumberColumn(format="%+.1f"),
                                    "repeat_sd": st.column_config.NumberColumn(format="%.1f")})
