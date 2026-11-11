import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import ui
from mqi import config, db
from mqi.analytics.spc import NELSON


def render():
    st.title(ui.t("nav_spc"))
    if not ui.has_data():
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    spec = config.specs()["product"]["properties"]
    tab1, tab2 = st.tabs(["Univariate", ui.t("t2_title")])
    lots = ui.lot_axis(b)

    with tab1:
        props = list(b.spc)
        cc1, cc2 = st.columns([4, 1])
        with cc1:
            p = st.segmented_control(ui.t("property"), props, default=props[0], format_func=ui.prop_label,
                                     label_visibility="collapsed") or props[0]
        cc2.toggle("4M 변경점" if ui.lang() == "ko" else "4M changes", value=True, key="spc_changes")
        s, x = b.spc[p], b.product[p].values
        lim = s["limits"]
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.5, 0.25, 0.25])
        fig.add_trace(go.Scatter(x=lots, y=x, mode="lines+markers", name=ui.prop_label(p),
                                 line=dict(color=ui.MUTED, width=1), marker=dict(size=4, color=ui.INK)), 1, 1)
        for v, dash in ((lim.center, "solid"), (lim.ucl, "dash"), (lim.lcl, "dash")):
            fig.add_hline(v, line_dash=dash, line_color=ui.MUTED, line_width=1, row=1, col=1)
        nl = s["nelson"]
        hit = nl.any(axis=1).values & (np.arange(len(x)) >= b.baseline)
        why = nl.apply(lambda r: ", ".join(f"R{k}" for k, v in r.items() if v), axis=1)
        fig.add_trace(go.Scatter(x=lots[hit], y=x[hit], mode="markers", name="Nelson", text=why[hit],
                                 hovertemplate="%{x}<br>%{text}", marker=dict(color=ui.RED, size=8, symbol="circle-open",
                                                                              line=dict(width=2))), 1, 1)
        ew = s["ewma"]
        fig.add_trace(go.Scatter(x=lots, y=ew["ewma"], name="EWMA", line=dict(color=ui.BLUE, width=2)), 2, 1)
        fig.add_trace(go.Scatter(x=lots, y=ew["ucl"], name="EWMA UCL", line=dict(color=ui.BLUE, dash="dot", width=1),
                                 showlegend=False), 2, 1)
        fig.add_trace(go.Scatter(x=lots, y=ew["lcl"], line=dict(color=ui.BLUE, dash="dot", width=1),
                                 showlegend=False), 2, 1)
        cu = s["cusum"]
        fig.add_trace(go.Scatter(x=lots, y=cu["cusum_hi"], name="CUSUM+", line=dict(color=ui.AMBER)), 3, 1)
        fig.add_trace(go.Scatter(x=lots, y=-cu["cusum_lo"], name="CUSUM−", line=dict(color=ui.GREEN)), 3, 1)
        h = cu["h"].iloc[0]
        fig.add_hline(h, line_dash="dot", line_color=ui.MUTED, row=3, col=1)
        fig.add_hline(-h, line_dash="dot", line_color=ui.MUTED, row=3, col=1)
        fig.add_vrect(x0=lots.iloc[0], x1=lots.iloc[b.baseline - 1], fillcolor="#f3f4f6", line_width=0, layer="below")
        show_chg = st.session_state.get("spc_changes", True)
        chg = db.read("SELECT change_no, date, category, item FROM changes", (), b.dbpath) if show_chg else None
        if chg is not None and len(chg):
            made = pd.to_datetime(b.product["measured_at"]).values
            for _, c in chg.iterrows():
                k = np.searchsorted(made, np.datetime64(pd.to_datetime(c["date"])))
                if 0 < k < len(lots):
                    fig.add_vline(lots.iloc[k], line_dash="dot", line_color=ui.AMBER, line_width=1)
                    fig.add_annotation(x=lots.iloc[k], y=1.0, yref="y domain", text=f"{c['category'][:4]} · {c['item']}",
                                       showarrow=False, textangle=-90, xanchor="right", yanchor="top",
                                       font=dict(size=9, color=ui.AMBER), row=1, col=1)
        fig.update_layout(template="plotly_white", height=560, margin=dict(l=10, r=10, t=20, b=10),
                          legend=dict(orientation="h", y=1.04), hovermode="x unified")
        fig.update_xaxes(showticklabels=False)
        st.plotly_chart(fig, width="stretch")

        first = s["first"]
        three = first.get("3σ (rule 1)")
        rows = []
        for k, v in first.items():
            rows.append({ui.t("method"): k, ui.t("first_lot"): lots.iloc[v] if v is not None else "-",
                         ui.t("lots_earlier"): (three - v) if (v is not None and three is not None and k != "3σ (rule 1)")
                         else None})
        c1, c2 = st.columns([2, 3])
        c1.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={ui.t("lots_earlier"): st.column_config.NumberColumn(format="%+d")})
        c2.caption(" · ".join(f"R{k} {v}" for k, v in NELSON.items()))

    with tab2:
        t2 = b.t2
        if t2 is None:
            return
        fig = ui.fig_base(300)
        ok = t2["alarm"] & t2["univariate_ok"]
        fig.add_trace(go.Scatter(x=lots, y=t2["t2"], mode="lines+markers", name="T²",
                                 line=dict(color=ui.MUTED, width=1), marker=dict(size=4, color=ui.INK)))
        fig.add_trace(go.Scatter(x=lots[t2["alarm"]], y=t2["t2"][t2["alarm"]], mode="markers", name="T² alarm",
                                 marker=dict(color=ui.AMBER, size=8)))
        fig.add_trace(go.Scatter(x=lots[ok], y=t2["t2"][ok], mode="markers", name=ui.t("hidden"),
                                 marker=dict(color=ui.RED, size=12, symbol="diamond")))
        fig.add_hline(t2["ucl"].iloc[-1], line_dash="dash", line_color=ui.RED, annotation_text="UCL")
        fig.update_xaxes(showticklabels=False)
        st.plotly_chart(fig, width="stretch")
        alarms = lots[t2["alarm"]].tolist()
        if alarms:
            default = lots[ok].tolist()[0] if ok.any() else alarms[0]
            sel = st.selectbox("Lot", alarms, index=alarms.index(default))
            i = lots[lots == sel].index[0]
            cc = t2.loc[i, [c for c in t2.columns if c.startswith("c_")]]
            cc.index = [ui.prop_label(c[2:]) for c in cc.index]
            bar = ui.fig_base(240, title=dict(text=f"{sel} · {ui.t('contrib')}", font_size=13))
            bar.add_trace(go.Bar(x=cc.index, y=cc.values, marker_color=[ui.RED if v > 0 else ui.BLUE for v in cc.values]))
            st.plotly_chart(bar, width="stretch")
