"""One self-contained HTML report: headline, findings, capability, supplier trust, 8D draft."""
from __future__ import annotations

import html
from datetime import datetime

import markdown_it

from . import config, engine
from .insight import drafts, eightd, rules
from .insight.rules import lab, num

_md = markdown_it.MarkdownIt("commonmark").enable("table")

STYLE = """
body{font-family:Inter,Pretendard,Segoe UI,sans-serif;color:#1f2937;max-width:960px;margin:32px auto;padding:0 16px}
h1{font-size:22px;margin-bottom:2px} h2{font-size:16px;margin-top:28px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}
.muted{color:#6b7280;font-size:12px} table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{border-bottom:1px solid #e5e7eb;padding:6px 8px;text-align:left} th{background:#f9fafb}
.kpi{display:inline-block;margin:6px 18px 6px 0}.kpi b{display:block;font-size:20px}
.chip{padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600}
.high{background:#fee2e2;color:#991b1b}.medium{background:#fef3c7;color:#92400e}.low{background:#d1fae5;color:#065f46}
"""


def build(b: engine.Bundle, lang: str = "ko") -> str:
    ko = lang == "ko"
    h = engine.health(b)
    s = config.specs()["product"]
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>MQI report</title><style>{STYLE}</style></head><body>"]
    out.append(f"<h1>{s['name']} — {'품질 현황 보고서' if ko else 'Quality status report'}</h1>")
    out.append(f"<div class='muted'>{datetime.now():%Y-%m-%d %H:%M} · Material Quality Intelligence</div>")
    kp = [(("제품 로트", "Product lots"), h["lots"]),
          (("최저 Ppk", "Lowest Ppk"), f"{h['worst_ppk']:.2f} ({lab(h['worst_prop'], lang)})" if h["worst_ppk"] else "-"),
          (("조기경보", "Early warnings"), h["spc_alarms"]), (("숨은 이상(T²)", "Hidden T²"), h["t2_hidden"]),
          (("신뢰 불가 CoA", "Untrusted CoA"), h["untrusted"]),
          (("%GRR", "%GRR"), f"{h['grr']:.1f}%" if h["grr"] else "-")]
    out.append("<div>" + "".join(f"<span class='kpi'>{k[0] if ko else k[1]}<b>{v}</b></span>" for k, v in kp) + "</div>")

    out.append(f"<h2>{'우선 조치' if ko else 'Priority actions'}</h2><table><tr><th></th><th>{'내용' if ko else 'Finding'}</th>"
               f"<th>{'근거' if ko else 'Evidence'}</th><th>{'조치' if ko else 'Action'}</th></tr>")
    for f in rules.findings(b):
        out.append(f"<tr><td><span class='chip {f.severity}'>{f.severity}</span></td><td>{html.escape(f.t('title', lang))}</td>"
                   f"<td>{html.escape(f.t('evidence', lang))}</td><td>{html.escape(f.t('action', lang))}</td></tr>")
    out.append("</table>")

    out.append(f"<h2>{'공정능력' if ko else 'Capability'}</h2><table><tr><th></th><th>Cpk</th><th>Ppk</th><th>PPM</th><th>n</th></tr>")
    for p, c in b.cap.items():
        out.append(f"<tr><td>{lab(p, lang)}</td><td>{c.cpk:.2f}</td><td>{c.ppk:.2f}</td><td>{c.ppm:,.0f}</td><td>{c.n}</td></tr>")
    out.append("</table>")

    if b.trust is not None:
        out.append(f"<h2>{'공급사 CoA 신뢰도' if ko else 'Supplier CoA trust'}</h2><table><tr><th>Supplier</th>"
                   "<th></th><th>n</th><th>bias</th><th>p</th><th>trust</th></tr>")
        for _, t in b.trust.iterrows():
            out.append(f"<tr><td>{t['supplier']}</td><td>{lab(t['property'], lang)}</td><td>{t['n']}</td>"
                       f"<td>{num(t['bias'])}</td><td>{t['p_value']:.3f}</td><td>{t['trust']}</td></tr>")
        out.append("</table>")

    out.append(f"<h2>8D</h2>{eightd.fragment(drafts.eight_d(b, 'viscosity', lang))}")
    out.append("<p class='muted'>Synthetic demo data. Generated automatically; review before use.</p></body></html>")
    return "".join(out)
