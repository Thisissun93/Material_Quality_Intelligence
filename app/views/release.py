import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from mqi import config
from mqi.analytics import genealogy as gn
from mqi.insight.rules import num


def render():
    st.title(ui.t("nav_release"))
    b = ui.bundle() if ui.has_data() else None
    if not b or not b.models:
        st.info(ui.t("empty"))
        return
    spec = config.specs()["product"]["properties"]

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.subheader(ui.t("models"))
        q = pd.DataFrame([{ui.t("property"): ui.prop_label(k), "model": m.kind, "R² (CV)": m.r2,
                           "MAE (CV)": m.mae} for k, m in b.models.items()])
        st.dataframe(q, hide_index=True, width="stretch",
                     column_config={"R² (CV)": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
                                    "MAE (CV)": st.column_config.NumberColumn(format="%.3g")})
    with right:
        att = b.attribution.get("viscosity")
        if att is not None:
            st.subheader(ui.t("drivers"))
            fig = ui.fig_base(250)
            fig.add_trace(go.Bar(y=[ui.prop_label(f) for f in att["feature"]][::-1], x=att["effect"][::-1],
                                 orientation="h", marker_color=[ui.RED if v > 0 else ui.BLUE for v in att["effect"][::-1]],
                                 hovertext=[f"{num(a)} → {num(c)}" for a, c in zip(att["baseline"][::-1], att["alarm"][::-1])],
                                 text=[f"{num(a)} → {num(c)}" if abs(e) == att["effect"].abs().max() else ""
                                       for a, c, e in zip(att["baseline"][::-1], att["alarm"][::-1], att["effect"][::-1])],
                                 textposition="inside", insidetextanchor="end"))
            fig.update_layout(xaxis_title=f"Δ {ui.prop_label('viscosity')} ({spec['viscosity']['unit']})",
                              hovermode="closest")
            st.plotly_chart(fig, width="stretch")

    st.subheader(ui.t("whatif"))
    rf = gn.raw_features()
    lots = b.raw.drop_duplicates("lot_id").sort_values("made_at")
    pick = {}
    cols = st.columns(3)
    for col, mat in zip(cols, ("epoxy_resin", "silica_filler", "hardener")):
        ids = lots[lots["material"] == mat]["lot_id"].tolist()[::-1]
        sup = lots.set_index("lot_id")["supplier"]
        pick[mat] = col.selectbox(ui.VALUE_KO[mat] if ui.lang() == "ko" else config.specs()["raw_materials"][mat]["label"], ids,
                                  format_func=lambda i, s=sup: f"{i} · {s.get(i, '')}")
    feat = {}
    for lot in pick.values():
        if lot in rf.index:
            feat.update({k: v for k, v in rf.loc[lot].items() if k in gn.FEATURES and pd.notna(v)})
    with st.expander("Override", expanded=False):
        oc = st.columns(len(gn.FEATURES))
        for c, f in zip(oc, gn.FEATURES):
            if f in feat:
                feat[f] = c.number_input(ui.prop_label(f), value=float(feat[f]), format="%.4g")
    X = pd.DataFrame([feat])
    if X.isna().any(axis=None) or not set(gn.FEATURES) <= set(X.columns):
        st.warning("missing raw data")
        return
    pr = gn.predict(b.models, X).iloc[0]
    mc = st.columns(len(b.models) + 1)
    watch = config.rules()["release"]
    for c, (k, m) in zip(mc, b.models.items()):
        po = pr[f"p_oos_{k}"]
        ui.kpi(c, ui.prop_label(k), num(pr[k]), f"95% ±{num(1.96 * m.resid_sd)} · {ui.t('risk')} {po:.1%}",
               "bad" if po > watch["risk_block"] else "warn" if po > watch["risk_watch"] else "")
    ui.kpi(mc[-1], ui.t("risk"), f"{pr['risk']:.1%}",
           tone="bad" if pr["risk"] > watch["risk_block"] else "warn" if pr["risk"] > watch["risk_watch"] else "")

    if b.matching is not None and len(b.matching):
        st.subheader(ui.t("matching"))
        show = ["resin_lot", "filler_lot", "hardener_lot", "viscosity", "risk"]
        top = max(0.2, float(b.matching["risk"].max()))
        cfg = {}
        for k in ("viscosity", "점도", "Viscosity"):
            cfg[k] = st.column_config.NumberColumn(format="%.0f")
        for k in ("risk", "이탈 확률", "OOS risk"):
            cfg[k] = st.column_config.ProgressColumn(min_value=0, max_value=top, format="percent")
        c1, c2 = st.columns(2)
        c1.caption(ui.t("best"))
        c1.dataframe(ui.localize(b.matching[show].head(5)), hide_index=True, width="stretch", column_config=cfg)
        c2.caption(ui.t("worst"))
        c2.dataframe(ui.localize(b.matching[show].tail(5).iloc[::-1]), hide_index=True, width="stretch",
                     column_config=cfg)
