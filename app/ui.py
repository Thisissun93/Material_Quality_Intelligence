"""Shared UI helpers: language, cached analysis, chart style, small components."""
from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mqi import config, engine  # noqa: E402
from mqi.i18n import t as _t  # noqa: E402
from mqi.insight.rules import LABEL  # noqa: E402

INK, MUTED, LINE = "#1f2937", "#6b7280", "#e5e7eb"
BLUE, RED, AMBER, GREEN = "#2563eb", "#dc2626", "#d97706", "#059669"

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1280px;}
h1 {font-size: 1.6rem !important; font-weight: 650;}
h2 {font-size: 1.2rem !important; font-weight: 600;}
h3 {font-weight: 600;}
[data-testid="stMetricValue"] {font-size: 1.6rem;}
[data-testid="stMetricLabel"] p {color: #6b7280;}
.chip {display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.78rem;font-weight:600;}
.chip.high {background:#fee2e2;color:#991b1b}
.chip.medium {background:#fef3c7;color:#92400e}
.chip.low, .chip.ok {background:#d1fae5;color:#065f46}
.card {border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:10px;background:#fff}
.card .t {font-weight:600;margin:4px 0 6px 0}
.card .e {color:#374151;font-size:0.9rem}
.card .a {color:#1d4ed8;font-size:0.9rem;margin-top:4px}
.muted {color:#6b7280;font-size:0.85rem}
h3 {font-size:1.05rem !important; margin-top:0.6rem;}
.kpi {padding:2px 0 6px 0}
.kpi .l {color:#6b7280;font-size:0.82rem}
.kpi .v {font-size:1.55rem;font-weight:600;line-height:1.25}
.kpi .s {color:#6b7280;font-size:0.78rem}
.kpi .v.bad {color:#b91c1c} .kpi .v.warn {color:#b45309}
</style>
"""


def lang() -> str:
    return st.session_state.get("lang", "ko")


def t(key: str, **kw) -> str:
    return _t(key, lang(), **kw)


def prop_label(p: str) -> str:
    ko, en = LABEL.get(p, (p, p))
    return ko if lang() == "ko" else en


def _stamp() -> tuple:
    db = config.DB_PATH
    cfg = tuple(f.stat().st_mtime for f in config.CONFIG_DIR.glob("*.yaml"))
    return (db.stat().st_mtime if db.exists() else 0, cfg)


@st.cache_resource(show_spinner=False)
def _bundle(stamp):
    config.reload()
    return engine.run()


def bundle():
    return _bundle(_stamp())


def clear():
    _bundle.clear()


def has_data() -> bool:
    return config.DB_PATH.exists() and len(bundle().product) > 0


def chip(sev: str) -> str:
    return f'<span class="chip {sev}">{t(sev)}</span>'


def fig_base(height: int = 320, **kw) -> go.Figure:
    f = go.Figure()
    f.update_layout(template="plotly_white", height=height, margin=dict(l=10, r=10, t=30, b=10),
                    font=dict(family="Inter, Pretendard, sans-serif", size=12, color=INK),
                    legend=dict(orientation="h", y=1.08, x=0), hovermode="x unified", **kw)
    f.update_xaxes(showgrid=False, linecolor=LINE)
    f.update_yaxes(gridcolor="#f3f4f6", linecolor=LINE)
    return f


def spec_lines(f: go.Figure, spec: dict, axis: str = "y"):
    for k, c in (("lsl", RED), ("usl", RED), ("target", MUTED)):
        v = spec.get(k)
        if v is None:
            continue
        kw = dict(line_color=c, line_dash="dot" if k == "target" else "dash", line_width=1,
                  annotation_text=k.upper(), annotation_font_color=c, annotation_font_size=10)
        (f.add_hline if axis == "y" else f.add_vline)(v, **kw)


def kpi(col, label: str, value, sub: str | None = None, tone: str = ""):
    col.markdown(f'<div class="kpi"><div class="l">{label}</div><div class="v {tone}">{value}</div>'
                 + (f'<div class="s">{sub}</div>' if sub else "") + "</div>", unsafe_allow_html=True)


VALUE_KO = {"untrusted": "신뢰 불가", "verify": "확인 필요", "trusted": "신뢰", "tightened": "강화",
            "normal": "보통", "skip-lot": "Skip-lot", "epoxy_resin": "에폭시 수지", "silica_filler": "실리카 필러",
            "hardener": "아민 경화제", "product": "제품"}

COL_KO = {"supplier": "공급사", "material": "자재", "lots": "로트 수", "min_ppk": "최저 Ppk",
          "worst_property": "취약 항목", "oos_lots": "규격 이탈", "drifting": "이동 항목", "coa_trust": "CoA 신뢰도",
          "inspection": "검사 수준", "property": "항목", "n": "n", "bias": "평균 차이(CoA−자사)",
          "bias_pct_of_halfspec": "차이/½규격 %", "p_value": "p", "corr": "상관", "loa": "±LoA",
          "drift_since": "차이 확대 시작", "trust": "신뢰도", "resin_lot": "수지 로트", "filler_lot": "필러 로트",
          "hardener_lot": "경화제 로트", "viscosity": "점도", "tg": "Tg", "risk": "이탈 확률"}


COL_EN = {"supplier": "Supplier", "material": "Material", "lots": "Lots", "min_ppk": "Min Ppk",
          "worst_property": "Weakest", "oos_lots": "OOS", "drifting": "Drifting", "coa_trust": "CoA trust",
          "inspection": "Inspection", "property": "Property", "bias": "Bias (CoA−in-house)",
          "bias_pct_of_halfspec": "Bias / ½spec %", "p_value": "p", "corr": "Corr", "loa": "±LoA",
          "drift_since": "Gap growing since", "trust": "Trust", "resin_lot": "Resin lot", "filler_lot": "Filler lot",
          "hardener_lot": "Hardener lot", "viscosity": "Viscosity", "tg": "Tg", "risk": "OOS risk"}
VALUE_EN = {"epoxy_resin": "Epoxy resin", "silica_filler": "Silica filler", "hardener": "Amine hardener"}


def localize(df):
    """Translate column names, property codes and category values when the UI is Korean."""
    import pandas as pd
    d = df.copy()
    for c in d.columns:
        if not pd.api.types.is_numeric_dtype(d[c]):
            d[c] = d[c].map(lambda v: (prop_label(v) if v in LABEL else VALUE_KO.get(v, v)) if lang() == "ko"
                            else (LABEL.get(v, (v, v))[1] if v in LABEL else VALUE_EN.get(v, v))
                            if isinstance(v, str) else v)
            d[c] = d[c].where(pd.notna(d[c]), "")
    return d.rename(columns=COL_KO if lang() == "ko" else COL_EN)


def lot_axis(b):
    return b.product["lot_id"]
