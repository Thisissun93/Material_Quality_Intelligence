import plotly.graph_objects as go
import streamlit as st

import ui
from mqi import config
from mqi.ingest import coa_docs
from mqi.analytics.supplier import paired
from mqi.insight import drafts, eightd


def render():
    st.title(ui.t("nav_supplier"))
    if not ui.has_data() or ui.bundle().score is None:
        st.info(ui.t("empty"))
        return
    b = ui.bundle()
    ko = ui.lang() == "ko"
    t1, t2 = st.tabs(["성적표 · CoA 신뢰도" if ko else "Scorecard · CoA trust", "CoA 문서" if ko else "CoA documents"])
    with t1:
        _overview(b)
    with t2:
        _documents(b, ko)


def _overview(b):
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
        st.html(eightd.fragment(text))
        st.download_button(ui.t("download"), text, file_name=f"SCAR_{sup}_{prop}.md")


VERDICT = {"ok": ("일치", "Match"), "gap": ("차이 큼", "Large gap"), "inhouse_oos": ("자사 규격 이탈", "In-house OOS"),
           "no_inhouse": ("자사 측정 없음", "No in-house data")}


def _documents(b, ko):
    docs = coa_docs.documents(b.dbpath)
    if docs.empty:
        st.info(ui.t("empty"))
        return
    flag = 25.0
    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    sups = c1.multiselect("공급사" if ko else "Supplier", sorted(docs["supplier"].dropna().unique()),
                          placeholder="전체" if ko else "All")
    only = c2.toggle(f"차이 큰 문서만 (>{flag:.0f}% ½규격)" if ko else f"Only large gaps (>{flag:.0f}% ½spec)")
    d = docs[docs["supplier"].isin(sups)] if sups else docs
    if only:
        d = d[d["gap_pct"] > flag]
    c3.metric("문서" if ko else "Documents", f"{len(d)} / {len(docs)}")

    left, right = st.columns([1, 1.35])
    with left:
        show = d[["lot_id", "supplier", "made_at", "items", "gap_pct"]].rename(columns={
            "lot_id": "Lot", "supplier": "공급사" if ko else "Supplier", "made_at": "제조일" if ko else "Made",
            "items": "항목" if ko else "Items", "gap_pct": "최대 차이 %½규격" if ko else "Max gap %½spec"})
        ev = st.dataframe(show, hide_index=True, width="stretch", height=560, on_select="rerun",
                          selection_mode="single-row", key="coa_pick",
                          column_config={show.columns[-1]: st.column_config.ProgressColumn(
                              format="%.0f", min_value=0, max_value=100)})
        rows = ev.selection.rows if ev and ev.selection else []
        pick = d.iloc[rows[0]] if rows else d.loc[d["gap_pct"].fillna(-1).idxmax()]
    with right:
        st.markdown(f"**{pick['lot_id']}** · {pick['supplier']} · `{pick['file']}`")
        cmp_ = coa_docs.compare(pick["file"], b.dbpath, flag)
        lab = (lambda k: VERDICT[k][0]) if ko else (lambda k: VERDICT[k][1])
        tbl = cmp_.assign(property=cmp_["property"].map(ui.prop_label), verdict=cmp_["verdict"].map(lab),
                          spec=cmp_.apply(lambda r: f"{r['lsl']}–{r['usl']}", axis=1))[
            ["property", "unit", "coa", "inhouse", "gap", "spec", "verdict"]]
        tbl.columns = (["항목", "단위", "CoA", "자사", "차이", "규격", "판정"] if ko else
                       ["Item", "Unit", "CoA", "In-house", "Gap", "Spec", "Verdict"])

        def color(v):
            return {"차이 큼": "background-color:#fef3c7", "Large gap": "background-color:#fef3c7",
                    "자사 규격 이탈": "background-color:#fee2e2", "In-house OOS": "background-color:#fee2e2"}.get(v, "")
        st.dataframe(tbl.style.map(color, subset=[tbl.columns[-1]]).format(
            {tbl.columns[2]: "{:.4g}", tbl.columns[3]: "{:.4g}", tbl.columns[4]: "{:+.3g}"}, na_rep="-"),
            hide_index=True, width="stretch")
        path = coa_docs.locate(pick["file"])
        if path is None:
            st.caption("원본 파일을 찾을 수 없음" if ko else "Original file not found")
        elif path.suffix.lower() == ".pdf":
            for im in coa_docs.pages(str(path)):
                st.image(im, width="stretch")
            st.download_button("원본 PDF" if ko else "Original PDF", path.read_bytes(), file_name=path.name,
                               mime="application/pdf")
