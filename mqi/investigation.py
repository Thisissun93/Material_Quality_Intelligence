"""Investigation pack: everything a quality engineer gathers for one lot when a complaint or OOS comes in.

In a plant this means opening several systems one by one: the lot's release status, LIMS test history
(including first results and retests), OOS records, MES genealogy (which raw-material lots went in, from
which supplier), incoming inspection and supplier CoA data for those raw lots, the MES batch record,
equipment runs and FDC alarms, 4M changes around the production date, other product lots that used the
same raw lots (containment scope), and finally an 8D draft. This module collects all of it in one call,
counts what was touched, and times each part (see scripts/benchmark_investigation.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from . import db, fdc, oos, outgoing, qms
from .analytics import changes
from .insight import eightd

# (key, Korean label, systems the data normally lives in; empty = produced by the app, not looked up)
SECTIONS = [
    ("release", "출하 판정과 사유", ()),
    ("history", "검사 이력 (최초·재시험)", ("LIMS",)),
    ("oos", "OOS 조사", ("QMS",)),
    ("genealogy", "투입 원료 로트", ("MES",)),
    ("raw", "원료 수입검사·CoA 값", ("LIMS", "공급사 CoA")),
    ("coa_trust", "해당 공급사 CoA 신뢰도", ("공급사 CoA",)),
    ("process", "배치 공정 기록", ("MES",)),
    ("equipment", "설비 런·FDC 알람", ("설비 FDC",)),
    ("changes", "생산일 전 60일 4M 변경점", ("변경 이력",)),
    ("same_raw", "같은 원료를 쓴 다른 제품 로트", ("MES",)),
    ("complaints", "고객 클레임", ("QMS",)),
    ("eightd", "고객 클레임 8D 초안 (클레임이 있는 로트만)", ()),
]


@dataclass
class Investigation:
    lot: str
    prop: str
    made_at: str | None = None
    level: str = ""
    reasons: list = field(default_factory=list)
    data: dict = field(default_factory=dict)        # key -> DataFrame (or str for eightd)
    seconds: dict = field(default_factory=dict)     # key -> seconds spent collecting that part

    @property
    def records(self) -> dict:
        out = {}
        for k, v in self.data.items():
            out[k] = len(v) if isinstance(v, pd.DataFrame) else int(bool(v))
        return out

    @property
    def systems(self) -> list[str]:
        """Systems that actually returned data for this lot."""
        rec = self.records
        return sorted({s for k, _, systems in SECTIONS if rec.get(k) for s in systems})

    @property
    def n_records(self) -> int:
        return sum(v for k, v in self.records.items() if k not in ("release", "eightd"))

    @property
    def total_seconds(self) -> float:
        return sum(self.seconds.values())


def _timed(inv: Investigation, key: str, fn):
    t = time.perf_counter()
    v = fn()
    inv.seconds[key] = time.perf_counter() - t
    inv.data[key] = v
    return v


def collect(b, lot: str, prop: str | None = None, complaint: dict | None = None, lang: str = "ko") -> Investigation:
    """Gather the investigation pack for one product lot.

    b: engine.Bundle (analysis already run). prop: the property in question; defaults to the complaint's
    property, otherwise viscosity. complaint: a row from qms.complaints(); if omitted, the newest open
    complaint on this lot is used when there is one.
    """
    cc = qms.complaints(b.dbpath)
    on_lot = cc[cc["lot_id"] == lot] if len(cc) else cc
    if complaint is None and len(on_lot):
        complaint = on_lot.iloc[0].to_dict()
    prop = prop or (complaint or {}).get("property") or "viscosity"
    inv = Investigation(lot=lot, prop=prop)

    def release():
        level, reasons = outgoing.release_check(b, lot)
        inv.level, inv.reasons = level, reasons
        return pd.DataFrame(reasons, columns=["ko", "en"])

    _timed(inv, "release", release)
    _timed(inv, "history", lambda: oos.history(lot, dbpath=b.dbpath))
    _timed(inv, "oos", lambda: db.read("SELECT * FROM oos WHERE lot_id=? ORDER BY id", (lot,), b.dbpath))
    gen = _timed(inv, "genealogy", lambda: db.read(
        "SELECT g.material, g.raw_lot, l.supplier, l.made_at FROM genealogy g "
        "LEFT JOIN lots l ON l.lot_id = g.raw_lot WHERE g.product_lot=? ORDER BY g.material", (lot,), b.dbpath))
    raw_lots = tuple(gen["raw_lot"]) if len(gen) else ()

    def raw():
        if not raw_lots:
            return pd.DataFrame()
        q = ",".join("?" * len(raw_lots))
        return db.read(f"SELECT lot_id, material, property, value, unit, source, measured_at FROM measurements "
                       f"WHERE lot_id IN ({q}) ORDER BY lot_id, property, source", raw_lots, b.dbpath)

    _timed(inv, "raw", raw)

    def trust():
        t = b.trust if b.trust is not None else pd.DataFrame()
        if t.empty or gen.empty:
            return pd.DataFrame()
        return t[t["supplier"].isin(gen["supplier"].dropna())].reset_index(drop=True)

    _timed(inv, "coa_trust", trust)
    _timed(inv, "process", lambda: db.read("SELECT param, value, text FROM process WHERE lot_id=? ORDER BY param",
                                           (lot,), b.dbpath))

    def equipment():
        r = fdc.runs(lot_id=lot, dbpath=b.dbpath)
        if r.empty:
            return r
        ids = tuple(int(i) for i in r["id"])
        a = db.read(f"SELECT run_id, step, rule, tag, value, lim, message_ko FROM fdc_alarms WHERE run_id IN "
                    f"({','.join('?' * len(ids))})", ids, b.dbpath)
        r = r[["id", "equipment", "started", "ended", "status"]].copy()
        r["alarms"] = [" / ".join(a.loc[a["run_id"] == i, "message_ko"].astype(str)) for i in r["id"]]
        return r

    _timed(inv, "equipment", equipment)

    def chg():
        if prop not in b.product:
            return pd.DataFrame()
        made = db.read("SELECT made_at FROM lots WHERE lot_id=?", (lot,), b.dbpath)
        day = str(made["made_at"].iloc[0])[:10] if len(made) and pd.notna(made["made_at"].iloc[0]) else None
        if not day:                                   # no lot master date: fall back to the first test date
            row = b.product[b.product["lot_id"] == lot]
            if row.empty:
                return pd.DataFrame()
            day = str(row["measured_at"].iloc[0])[:10]
        inv.made_at = day
        c = changes.changes_near(b, prop, day, before=60, after=0)
        keep = [k for k in ("change_no", "date", "category", "item", "description", "notified", "days_from_start")
                if k in c]
        return c[keep].reset_index(drop=True) if len(c) else c

    _timed(inv, "changes", chg)

    def same_raw():
        if not raw_lots:
            return pd.DataFrame()
        q = ",".join("?" * len(raw_lots))
        s = db.read(f"SELECT product_lot, material, raw_lot FROM genealogy WHERE raw_lot IN ({q}) AND product_lot<>? "
                    f"ORDER BY product_lot", (*raw_lots, lot), b.dbpath)
        return s

    _timed(inv, "same_raw", same_raw)
    _timed(inv, "complaints", lambda: on_lot.reset_index(drop=True) if len(on_lot) else pd.DataFrame())
    # The 8D builder is tied to a customer complaint (lot, symptom, containment). Without one it would
    # describe the plant-wide SPC alarm instead of this lot, so the draft is only made for a complaint.
    _timed(inv, "eightd", lambda: eightd.markdown(eightd.build(b, prop, lang=lang, complaint=complaint))
           if complaint else "")
    return inv


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v).replace("|", "/").replace("\n", " ")


def _table(df: pd.DataFrame) -> str:
    rows = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    rows += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def markdown(inv: Investigation, max_rows: int = 12) -> str:
    """Readable summary of a pack (Korean)."""
    L = [f"# 조사 패키지: {inv.lot} ({inv.prop})", "",
         f"- 출하 판정: **{inv.level}**, 생산일 {inv.made_at or '미상'}",
         f"- 데이터를 가져온 시스템 {len(inv.systems)}곳: {', '.join(inv.systems)}",
         f"- 모은 기록 {inv.n_records}건, "
         f"수집 시간 {inv.total_seconds:.2f}초", ""]
    for key, label, systems in SECTIONS:
        v = inv.data.get(key)
        if key == "eightd":
            if not v:
                L += [f"## {label}", "", "이 로트에는 고객 클레임이 없어 생략했습니다. 공정 전체 SPC 경보 기준 8D는 `보고서` 화면에 있습니다.", ""]
            continue
        n = len(v) if isinstance(v, pd.DataFrame) else 0
        L.append(f"## {label}{' · ' + ', '.join(systems) if systems else ''} ({n}건)")
        L.append("")
        if isinstance(v, pd.DataFrame) and n:
            L.append(_table(v.head(max_rows)))
            if n > max_rows:
                L.append(f"\n… 외 {n - max_rows}건")
        else:
            L.append("해당 없음")
        L.append("")
    return "\n".join(L)
