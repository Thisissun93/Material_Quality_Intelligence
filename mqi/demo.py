"""Synthetic but physically consistent demo plant.

Everything here is invented. It writes the files a real lab would produce
(several instrument makers, firmware versions, units, locales, supplier CoA PDFs)
so the whole pipeline is exercised end to end.

Built-in stories the analytics should find on their own:
  * NanoSphere silica D50 drifts down from its 7th lot; product viscosity creeps up.
  * NanoSphere CoA keeps reporting ~3.0 µm: the CoA no longer matches in-house data.
  * One SilicaWorks lot arrives with high moisture.
  * Product lot UF-2609-012 has normal single values but an abnormal combination (Hotelling T²).
  * Gage R&R: operator Park reads viscosity high (reproducibility > repeatability).
  * The lab swapped DSC instruments in mid-August; the new one reads Tg about 2 °C high.
"""
from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import config, db

GAGE_SEED = 1
PROC_SEED = 5

RNG = np.random.default_rng(20261111)
OPS = ["Kim", "Lee", "Park"]


def _workdays(start: date, n: int, step: float) -> list[date]:
    out, d, acc = [], start, 0.0
    while len(out) < n:
        if d.weekday() < 5:
            acc += 1
            if acc >= step:
                out.append(d)
                acc -= step
        d += timedelta(days=1)
    return out


def build_truth() -> dict[str, pd.DataFrame]:
    start = date(2026, 5, 4)
    # ---- raw material lots
    resin = []
    for i, d in enumerate(_workdays(start - timedelta(days=10), 20, 6.5)):
        sup = "Resinova" if i % 5 not in (1, 3) else "KoPolymer"
        eew = RNG.normal(187.5 if sup == "Resinova" else 189.0, 2.0 if sup == "Resinova" else 2.4)
        visc = RNG.normal(11500 if sup == "Resinova" else 12100, 550 if sup == "Resinova" else 750)
        resin.append(dict(lot_id=f"ER-{sup[:2].upper()}-{i + 1:03d}", material="epoxy_resin", supplier=sup,
                          made_at=d, eew=eew, resin_visc=visc))
    filler, ns = [], 0
    for i, d in enumerate(_workdays(start - timedelta(days=12), 24, 5.5)):
        sup = "SilicaWorks" if i % 2 == 0 else "NanoSphere"
        if sup == "NanoSphere":
            ns += 1
            drift = -0.10 * max(0, ns - 6)
            d50 = RNG.normal(3.05 + drift, 0.06)
        else:
            d50 = RNG.normal(3.00, 0.07)
        moist = RNG.normal(0.038, 0.006)
        if sup == "SilicaWorks" and i == 10:
            moist = 0.092
        filler.append(dict(lot_id=f"SF-{sup[:2].upper()}-{i + 1:03d}", material="silica_filler", supplier=sup,
                           made_at=d, d50=d50, moisture=max(moist, 0.01), ns_index=ns if sup == "NanoSphere" else 0))
    hard = []
    for i, d in enumerate(_workdays(start - timedelta(days=8), 12, 11)):
        hard.append(dict(lot_id=f"HD-AT-{i + 1:03d}", material="hardener", supplier="AmineTech", made_at=d,
                         amine_value=RNG.normal(540, 5)))
    resin, filler, hard = map(pd.DataFrame, (resin, filler, hard))

    # ---- product lots and genealogy (FIFO-ish: latest received lot before production)
    prods, gen = [], []
    for i, d in enumerate(_workdays(start, 120, 1.15)):
        lot = f"UF-{d:%y%m}-{sum(1 for p in prods if p['made_at'].month == d.month) + 1:03d}"
        pick = {}
        for name, t in (("epoxy_resin", resin), ("silica_filler", filler), ("hardener", hard)):
            avail = t[t["made_at"] <= d - timedelta(days=2)]
            pick[name] = avail.iloc[-1] if len(avail) else t.iloc[0]
        r, f, h = pick["epoxy_resin"], pick["silica_filler"], pick["hardener"]
        filler_pct = RNG.normal(60.0, 0.33)
        visc = (3480 + 1600 * (3.0 - f.d50) + 0.07 * (r.resin_visc - 11500) + 3500 * (f.moisture - 0.04)
                + 160 * (filler_pct - 60) + RNG.normal(0, 85))
        tg = 147 - 0.85 * (r.eew - 187.5) + 0.14 * (h.amine_value - 540) - 70 * (f.moisture - 0.04) + RNG.normal(0, 1.7)
        peak = 158 + 0.30 * (r.eew - 187.5) - 0.05 * (h.amine_value - 540) + RNG.normal(0, 1.1)
        dh = 220 - 2.0 * (r.eew - 187.5) - 4.0 * (filler_pct - 60) + RNG.normal(0, 5.5)
        prods.append(dict(lot_id=lot, material="product", supplier="In-house", made_at=d,
                          viscosity=visc, tg=tg, peak_temp=peak, enthalpy=dh, filler_pct=filler_pct))
        for m, raw in pick.items():
            gen.append(dict(product_lot=lot, material=m, raw_lot=raw.lot_id))
    prods = pd.DataFrame(prods)
    # abnormal combination, every single value still in spec
    k = prods.index[prods["lot_id"] == "UF-2609-012"]
    if len(k):
        prods.loc[k, ["tg", "enthalpy", "peak_temp"]] += [-6.5, 8, -2.6]
    return dict(resin=resin, filler=filler, hard=hard, product=prods, genealogy=pd.DataFrame(gen))


# ---- file writers -------------------------------------------------------------

def _ts(d: date, hour: int = 9) -> datetime:
    return datetime(d.year, d.month, d.day, hour, int(RNG.integers(0, 59)))


def _week(d) -> str:
    d = pd.Timestamp(d)
    return (d - pd.Timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def write_files(t: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    p, gen = t["product"], t["genealogy"]
    raws = pd.concat([t["resin"], t["filler"], t["hard"]])

    # ERP lot master
    lm = pd.concat([raws, p])[["lot_id", "material", "supplier", "made_at"]]
    lm = lm.assign(**{"Material Code": lm["material"]})
    lm.rename(columns={"lot_id": "Lot No", "supplier": "Vendor", "made_at": "Date"})[
        ["Lot No", "Material Code", "Vendor", "Date"]].to_csv(out / "ERP_lot_master.csv", index=False)

    # MES genealogy
    g = gen.pivot(index="product_lot", columns="material", values="raw_lot")
    g = g[["epoxy_resin", "silica_filler", "hardener"]].reset_index()
    g.columns = ["Batch", "Resin Lot", "Filler Lot", "Hardener Lot"]
    g[["Batch", "Resin Lot", "Filler Lot", "Hardener Lot"]].to_csv(out / "MES_genealogy.csv", index=False)

    # MES process record (own random stream so the measured data stay identical)
    prng = np.random.default_rng(PROC_SEED)
    pr = []
    for k, (_, r) in enumerate(t["product"].iterrows()):
        mixer = "M1" if k % 3 != 2 else "M2"
        pr.append([r.lot_id, mixer, f"{prng.normal(45, 1.5):.0f}", f"{prng.normal(5.0 if mixer == 'M1' else 5.4, 0.5):.1f}",
                   f"{prng.normal(60, 0.8):.1f}", f"{prng.choice([200, 200, 400]):d}"])
    pd.DataFrame(pr, columns=["Batch", "Mixer", "Mix Time (min)", "Vacuum (mbar)", "Degas Temp (C)",
                              "Batch Size (kg)"]).to_csv(out / "MES_process_record.csv", index=False)

    # QMS 4M change log. The supplier's process change is NOT in it: nobody was told.
    pd.DataFrame([
        ["CN-26-031", "2026-05-18", "Man", "QC lab", "New analyst (Park) qualified for viscosity testing", "QC", "Y"],
        ["CN-26-038", "2026-06-15", "Method", "SOP-QC-012", "Viscosity SOP rev.C: spindle warm-up 5 → 10 min", "QC", "Y"],
        ["CN-26-044", "2026-07-08", "Machine", "Mixer M2", "Planetary blade replaced after overhaul", "Production", "Y"],
        ["CN-26-052", "2026-08-17", "Measurement", "DSC", "ThermaTech DSC replaced by CaloriX DSC", "QC", "Y"],
        ["CN-26-057", "2026-09-02", "Material", "AmineTech hardener", "Packaging changed from drum to IBC", "Purchasing", "Y"],
        ["CN-26-063", "2026-10-06", "Method", "Incoming inspection", "Sampling plan for resin lots updated", "QA", "Y"],
    ], columns=["Change No", "Date", "4M", "Item", "Description", "Owner", "Notified"]).to_csv(
        out / "QMS_4M_change_log.csv", index=False)

    # ---- product viscosity: three instruments
    p = p.copy()
    p["week"] = p["made_at"].map(_week)
    for wk, grp in p.groupby("week"):
        d0 = pd.Timestamp(wk).date()
        if d0 < date(2026, 7, 1):
            inst = "fw2"
        elif d0.isocalendar().week % 4 == 0:
            inst = "rheo"
        else:
            inst = "fw3"
        if inst == "fw3":
            lines = ["Instrument,RotaVisc DV-2", "Firmware,3.4.1", "Method,UF-VISC-25C", ""]
            lines.append("Sample ID,Spindle,Speed (RPM),Torque (%),Viscosity (cP),Temperature (C),Operator,Timestamp")
            for _, r in grp.iterrows():
                lines.append(f"{r.lot_id},SC4-14,20,{r.viscosity / 80:.1f},{r.viscosity:.0f},25.0,"
                             f"{RNG.choice(OPS)},{_ts(r.made_at):%Y-%m-%d %H:%M}")
            (out / f"DV2_{wk}.csv").write_text("\n".join(lines), encoding="utf-8")
        elif inst == "fw2":
            lines = ["Gerät;RotaVisc DV-2", "Firmware;2.9.0", ""]
            lines.append("Probe;Spindel;Drehzahl[rpm];Drehmoment[%];Visc[mPa.s];Temp[°C];Bediener;Datum")
            for _, r in grp.iterrows():
                v = f"{r.viscosity:.1f}".replace(".", ",")
                lines.append(f"{r.lot_id};SC4-14;20;{r.viscosity / 80:.1f}".replace(".", ",")
                             + f";{v};25,0;{RNG.choice(OPS)};{_ts(r.made_at):%d.%m.%Y %H:%M}")
            (out / f"DV2-legacy_{wk}.csv").write_text("\n".join(lines), encoding="cp1252")
        else:
            lines = ["RheoPro MCR 302", "Project\tUF-300 release", ""]
            lines.append("Sample\tShear Rate [1/s]\tViscosity [Pa·s]\tTemperature [°C]\tUser\tDate/Time")
            for _, r in grp.iterrows():
                for sr, fac in ((1, 1.35), (10, 1.0), (100, 0.72)):
                    lines.append(f"{r.lot_id}\t{sr}\t{r.viscosity * fac / 1000:.3f}\t25.0\t{RNG.choice(OPS)}\t"
                                 f"{_ts(r.made_at):%Y-%m-%dT%H:%M}")
            (out / f"MCR_{wk}.txt").write_text("\n".join(lines), encoding="utf-8")

    # ---- DSC: old summary reports until August, new instrument (Kelvin) after
    for _, r in p.iterrows():
        if r.made_at < date(2026, 8, 17):
            txt = (f"ThermaTech DSC Summary Report\nInstrument: TT-DSC250\nSample Name: {r.lot_id} UF-300\n"
                   f"Operator: {RNG.choice(OPS)}\nRun Date: {_ts(r.made_at, 13):%Y-%m-%d %H:%M}\n"
                   f"Method: Ramp 10 °C/min to 250 °C\nOnset Temperature: {r.peak_temp - 14:.1f} °C\n"
                   f"Peak Temperature: {r.peak_temp:.1f} °C\nEnthalpy (normalized): {r.enthalpy:.1f} J/g\n"
                   f"Tg (midpoint): {r.tg:.1f} °C\n")
            (out / f"DSC_{r.lot_id}.txt").write_text(txt, encoding="utf-8")
    late = p[p["made_at"] >= date(2026, 8, 17)]
    for wk, grp in late.groupby("week"):
        lines = ["CaloriX DSC 3+ export", "Method;Dyn 10K/min", ""]
        lines.append("Sample;Onset/K;Peak/K;Integral normalized/Jg^-1;Tg Midpoint/K;User;Date")
        for _, r in grp.iterrows():
            # the new DSC reads Tg and peak slightly high (different baseline / calibration)
            lines.append(f"{r.lot_id};{r.peak_temp - 14 + 273.15:.2f};{r.peak_temp + 1.3 + 273.15:.2f};"
                         f"{r.enthalpy:.1f};{r.tg + 2.2 + 273.15:.2f};{RNG.choice(OPS)};{_ts(r.made_at, 14):%Y-%m-%d}")
        (out / f"CaloriX_{wk}.csv").write_text("\n".join(lines), encoding="utf-8")

    # ---- TGA filler content
    for wk, grp in p.groupby("week"):
        lines = ["ThermoScale TGA", "Program,N2 to 800C", ""]
        lines.append("Sample,Residue (%),Onset (C),Operator,Date")
        for _, r in grp.iterrows():
            lines.append(f"{r.lot_id},{r.filler_pct:.2f},{380 + RNG.normal(0, 4):.1f},{RNG.choice(OPS)},"
                         f"{_ts(r.made_at, 15):%Y-%m-%d}")
        (out / f"TGA_{wk}.csv").write_text("\n".join(lines), encoding="utf-8")

    # ---- incoming inspection (in-house) on raw materials
    f = t["filler"].assign(month=lambda x: pd.to_datetime(x["made_at"]).dt.strftime("%Y-%m"))
    for m, grp in f.groupby("month"):
        lines = ["LaserSizer 3000", "SOP,IQC-PSD-02", ""]
        lines.append("Sample Name,D10 (µm),D50 (µm),D90 (µm),Span,Analyst,Measurement Date")
        for _, r in grp.iterrows():
            d50 = r.d50 + RNG.normal(0, 0.02)
            lines.append(f"{r.lot_id},{d50 * 0.45:.2f},{d50:.3f},{d50 * 1.9:.2f},{1.45:.2f},"
                         f"{RNG.choice(OPS)},{(r.made_at + timedelta(days=2)):%Y-%m-%d}")
        (out / f"PSD_incoming_{m}.csv").write_text("\n".join(lines), encoding="utf-8")
        lines = ["KF Coulometric C30", ""]
        lines.append("Sample,Water content [ppm],Drift,User,Date")
        for _, r in grp.iterrows():
            lines.append(f"{r.lot_id},{(r.moisture + RNG.normal(0, 0.002)) * 1e4:.0f},4.1,{RNG.choice(OPS)},"
                         f"{(r.made_at + timedelta(days=2)):%Y-%m-%d}")
        (out / f"KF_incoming_{m}.csv").write_text("\n".join(lines), encoding="utf-8")

    rh = pd.concat([t["resin"].assign(m="EEW", v=lambda x: x.eew, u="g/eq"),
                    t["hard"].assign(m="AMINE", v=lambda x: x.amine_value, u="mgKOH/g")])
    rh["month"] = pd.to_datetime(rh["made_at"]).dt.strftime("%Y-%m")
    for m, grp in rh.groupby("month"):
        lines = ["AutoTitrator T9 export", ""]
        lines.append("Sample ID,Method,Result,Unit,Analyst,End Time")
        for _, r in grp.iterrows():
            lines.append(f"{r.lot_id},{r.m},{r.v + RNG.normal(0, 0.6 if r.m == 'EEW' else 1.5):.1f},{r.u},"
                         f"{RNG.choice(OPS)},{(r.made_at + timedelta(days=2)):%Y-%m-%d %H:%M}")
        (out / f"Titration_{m}.csv").write_text("\n".join(lines), encoding="utf-8")
    rv = t["resin"].assign(month=lambda x: pd.to_datetime(x["made_at"]).dt.strftime("%Y-%m"))
    for m, grp in rv.groupby("month"):
        lines = ["Instrument,RotaVisc DV-2", "Firmware,3.4.1", "Method,IQC-RESIN-VISC", ""]
        lines.append("Sample ID,Spindle,Speed (RPM),Torque (%),Viscosity (cP),Temperature (C),Operator,Timestamp")
        for _, r in grp.iterrows():
            lines.append(f"{r.lot_id},SC4-27,10,{r.resin_visc / 300:.1f},{r.resin_visc + RNG.normal(0, 120):.0f},25.0,"
                         f"{RNG.choice(OPS)},{(r.made_at + timedelta(days=2)):%Y-%m-%d} 10:00")
        (out / f"DV2_resin_{m}.csv").write_text("\n".join(lines), encoding="utf-8")

    # ---- supplier CoA PDFs (two house styles, like real suppliers)
    for _, r in t["resin"].iterrows():
        _coa_pdf(out, r, "Bisphenol-A Liquid Epoxy Resin", "EP-128", 200, [
            ("Appearance", "Visual", "-", "Clear liquid", "Conforms"),
            ("Epoxy Equivalent Weight", "ASTM D1652", "g/eq", "180 - 195", r.eew + RNG.normal(0, 0.8)),
            ("Viscosity @25°C", "ASTM D445", "mPa·s", "9000 - 14000", r.resin_visc + RNG.normal(0, 150)),
            ("Hydrolyzable chlorine", "ASTM D1726", "ppm", "max 300", None)])
    for _, r in t["filler"].iterrows():
        if r.supplier == "NanoSphere":
            coa_d50 = 0.25 * r.d50 + 0.75 * 3.05 + RNG.normal(0, 0.03)  # CoA lags reality
            _coa_report(out, r, "Spherical Fused Silica", "NS-30F", 1000, [
                ("Mean particle size (D50)", "2.4~3.6", f"{coa_d50:.2f}", "µm"),
                ("Moisture content", "1000 max", f"{(r.moisture + RNG.normal(0, 0.003)) * 1e4:.0f}", "ppm"),
                ("Specific surface area (BET)", "1.0~3.0", f"{RNG.normal(2.1, 0.1):.1f}", "m2/g"),
                ("Appearance", "White powder", "OK", "")])
        else:
            _coa_pdf(out, r, "Spherical Silica Filler", "SW-3", 1000, [
                ("Appearance", "Visual", "-", "White powder", "Conforms"),
                ("D50", "ISO 13320", "µm", "2.4 - 3.6", r.d50 + RNG.normal(0, 0.03)),
                ("Moisture", "Karl Fischer", "%", "max 0.10", r.moisture + RNG.normal(0, 0.003)),
                ("Coarse particles +45µm", "Laser", "%", "max 0.01", None)])
    for _, r in t["hard"].iterrows():
        _coa_pdf(out, r, "Modified Aromatic Amine Hardener", "AT-540", 180, [
            ("Appearance", "Visual", "-", "Amber liquid", "Conforms"),
            ("Amine Value", "ASTM D2074", "mgKOH/g", "520 - 560", r.amine_value + RNG.normal(0, 1.2)),
            ("Color (Gardner)", "ASTM D1544", "-", "max 8", None)])

    # ---- Gage R&R study
    GRNG = np.random.default_rng(GAGE_SEED)  # own stream: other demo files must not change this study
    rows = []
    parts = np.linspace(2900, 4150, 10) + GRNG.normal(0, 60, 10)
    bias = {"Kim": 0, "Lee": 12, "Park": 72}
    for pi, true in enumerate(parts, 1):
        inter = {o: GRNG.normal(0, 14) for o in OPS}
        for o in OPS:
            for trial in (1, 2, 3):
                rows.append(f"{pi},{o},{trial},{true + bias[o] + inter[o] + GRNG.normal(0, 34):.0f}")
    (out / "GageRR_viscometer_2026-10.csv").write_text(
        "Gage R&R Study,RotaVisc DV-2 SN 7781\nCharacteristic,Viscosity @25C\n\n"
        "Part,Operator,Trial,Viscosity (cP)\n" + "\n".join(rows), encoding="utf-8")

    # ---- a new lab instrument nobody has configured yet
    sample = p.tail(6)
    lines = ["ViscoLab VL-9 data dump", ""]
    lines.append("Sample_No|Visco_mPas|Temp_C|Tested by|Test date")
    for _, r in sample.iterrows():
        lines.append(f"{r.lot_id}|{r.viscosity + RNG.normal(0, 40):.0f}|25.1|Lee|{_ts(r.made_at, 16):%Y/%m/%d}")
    (out / "ViscoLab_retest.txt").write_text("\n".join(lines), encoding="utf-8")


ADDR = {"Resinova": "12 Polymer Road, Ulsan, KR", "KoPolymer": "88 Chemical-ro, Yeosu, KR",
        "SilicaWorks": "5 Quartz Street, Penang, MY", "NanoSphere": "3-1 Kogyo-cho, Osaka, JP",
        "AmineTech": "200 Industrial Ave, Kaohsiung, TW"}


def _coa_pdf(out: Path, r, product: str, grade: str, qty: int, items) -> None:
    """Table-style Certificate of Analysis (letterhead, lot block, test table, conformity, sign-off)."""
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out / f"CoA_{r.lot_id}.pdf"), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"CoA {r.lot_id}")
    made = pd.Timestamp(r.made_at)
    head = Table([[Paragraph(f"<b><font size=15>{r.supplier}</font></b><br/><br/><font size=8>{r.supplier} Co., Ltd. · "
                             f"{ADDR.get(r.supplier, '')}</font>", st["Normal"]),
                   Paragraph("<font size=8>Doc. QA-F-021 Rev.3</font>", st["Normal"])]], colWidths=[130 * mm, 44 * mm])
    head.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.black), ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    info = [["Customer", "MQI Demo Plant", "Lot No.", r.lot_id],
            ["Product", product, "Grade", grade],
            ["Mfg. date", f"{made:%Y-%m-%d}", "Expiry date", f"{made + pd.Timedelta(days=365):%Y-%m-%d}"],
            ["Quantity", f"{qty} kg", "Test date", f"{made + pd.Timedelta(days=1):%Y-%m-%d}"]]
    ti = Table(info, colWidths=[26 * mm, 61 * mm, 26 * mm, 61 * mm])
    ti.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f1f5f9"))]))
    rows = [["No.", "Test item", "Method", "Unit", "Specification", "Result", "Judge"]]
    for k, (name, method, unit, spec, val) in enumerate(items, 1):
        if val is None:
            val = f"{RNG.uniform(0.2, 0.5) * float(spec.split()[-1]):.0f}" if spec.split()[-1].replace(".", "").isdigit() else "-"
        elif not isinstance(val, str):
            val = f"{val:.3g}" if abs(val) < 10 else f"{val:.1f}"
        rows.append([str(k), name, method, unit, spec, val, "Pass"])
    tt = Table(rows, colWidths=[10 * mm, 50 * mm, 27 * mm, 20 * mm, 30 * mm, 22 * mm, 15 * mm], repeatRows=1)
    tt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("ALIGN", (5, 1), (5, -1), "RIGHT")]))
    sign = Table([["Prepared by", "Approved by (QA Manager)", "Issue date"],
                  ["J. Lin", "S. Park", f"{made + pd.Timedelta(days=2):%Y-%m-%d}"]],
                 colWidths=[58 * mm, 58 * mm, 58 * mm], rowHeights=[6 * mm, 12 * mm])
    sign.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9),
                              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9"))]))
    doc.build([head, Spacer(1, 6 * mm), Paragraph("<b><font size=14>CERTIFICATE OF ANALYSIS</font></b>", st["Title"]),
               Spacer(1, 2 * mm), ti, Spacer(1, 6 * mm), tt, Spacer(1, 6 * mm),
               Paragraph("We hereby certify that the above lot has been inspected and conforms to the agreed specification.",
                         st["Normal"]), Spacer(1, 8 * mm), sign, Spacer(1, 30 * mm),
               Paragraph("<font size=7 color='#94a3b8'>Synthetic demo document generated by Material Quality Intelligence. "
                         "Not a real supplier certificate.</font>", st["Normal"])])


def _coa_report(out: Path, r, product: str, grade: str, qty: int, items) -> None:
    """Second house style: 'Inspection Report' with spec before result and unit after it."""
    c = canvas.Canvas(str(out / f"CoA_{r.lot_id}.pdf"), pagesize=A4)
    made = pd.Timestamp(r.made_at)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, 276 * mm, f"{r.supplier}")
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, 271 * mm, f"Supplier: {r.supplier} Co., Ltd. · {ADDR.get(r.supplier, '')}")
    c.line(20 * mm, 268 * mm, 190 * mm, 268 * mm)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(105 * mm, 256 * mm, "INSPECTION REPORT  (Quality Certificate)")
    c.setFont("Helvetica", 9.5)
    y = 244 * mm
    for a, b_, x2, d in (("Product", f"{product} {grade}", "Lot No.", r.lot_id),
                         ("Mfg. date", f"{made:%Y-%m-%d}", "Shelf life", "12 months"),
                         ("Quantity", f"{qty} kg (40 bags)", "Customer", "MQI Demo Plant")):
        c.drawString(20 * mm, y, f"{a}:")
        c.drawString(45 * mm, y, b_)
        c.drawString(110 * mm, y, f"{x2}:")
        c.drawString(135 * mm, y, d)
        y -= 6.5 * mm
    y -= 6 * mm
    c.setFont("Helvetica-Bold", 9.5)
    for x, h in ((20, "Inspection item"), (95, "Standard"), (135, "Result"), (160, "Unit")):
        c.drawString(x * mm, y, h)
    c.line(20 * mm, y - 2 * mm, 190 * mm, y - 2 * mm)
    c.setFont("Helvetica", 9.5)
    for name, std, val, unit in items:
        y -= 8 * mm
        c.drawString(20 * mm, y, name)
        c.drawString(95 * mm, y, std)
        c.drawString(135 * mm, y, val)
        c.drawString(160 * mm, y, unit)
    c.line(20 * mm, y - 3 * mm, 190 * mm, y - 3 * mm)
    y -= 14 * mm
    c.drawString(20 * mm, y, "Judgement: Accepted")
    c.drawString(20 * mm, y - 7 * mm, f"Inspector: K. Sato        Quality Assurance: H. Mori        Date: {made + pd.Timedelta(days=1):%Y-%m-%d}")
    c.setFont("Helvetica", 7)
    c.setFillGray(0.6)
    c.drawString(20 * mm, 15 * mm, "Synthetic demo document generated by Material Quality Intelligence. Not a real supplier certificate.")
    c.save()


# Bump when the planted scenarios change, so a deployed app rebuilds an older demo database.
DEMO_VERSION = "4"
OOS_DEMO_LOT = "UF-2610-014"   # the lot whose first result was re-measured without an investigation
_SIGNATURE = "UF-2609-012"          # a lot that only the demo plant has


def is_stale(dbpath=None) -> bool:
    """True for a demo database built by an older version (real plant data is never touched)."""
    if not db.exists(dbpath):
        return False
    try:
        if db.get_meta("demo_version", dbpath) == DEMO_VERSION:
            return False
        return bool(len(db.read("SELECT 1 FROM lots WHERE lot_id=? LIMIT 1", (_SIGNATURE,), dbpath)))
    except Exception:
        return False


def build(folder: Path | None = None, dbpath: Path | None = None, ingest: bool = True):
    """Generate the demo files and push them through the real ingest pipeline."""
    from .ingest.pipeline import process_folder

    folder = Path(folder or config.DATA_DIR / "demo_inbox")
    if folder.exists():
        shutil.rmtree(folder)
    write_files(build_truth(), folder)
    if not ingest:
        return []
    db.reset(dbpath)
    res = process_folder(folder, dbpath)
    complaint_no = seed_complaint(dbpath)
    seed_oos_retest(dbpath, complaint_no)
    db.set_meta("demo_version", DEMO_VERSION, dbpath)
    return res


def seed_oos_retest(dbpath=None, complaint_no: str | None = None) -> str | None:
    """A recent lot whose first viscosity result was over USL and was simply re-measured.

    The analyst Park (who reads high in the Gage R&R) reported 4,640 mPa·s; the lab re-measured the
    same sample and got the normal value, but no OOS investigation was opened. The app must keep the
    first result and block the lot instead of shipping on the passing retest.
    """
    from . import qms
    p = db.product_wide(path=dbpath)
    if p.empty or "viscosity" not in p:
        return None
    skip = {"UF-2609-012"}
    c = qms.complaint(complaint_no, dbpath) if complaint_no else None
    if c:
        skip.add(c["lot_id"])
    cand = p.tail(15)
    cand = cand[~cand["lot_id"].isin(skip)]
    # fixed lot so the README tour points at the same lot everywhere; fall back to the most typical recent lot
    fixed = cand[cand["lot_id"] == OOS_DEMO_LOT]
    lot = fixed.iloc[0] if len(fixed) else cand.iloc[(cand["viscosity"] - 3500).abs().argsort()].iloc[0]
    with db.connect(dbpath) as con:
        row = con.execute("SELECT measured_at, unit, instrument, profile FROM measurements WHERE lot_id=? AND "
                          "property='viscosity' AND source='inhouse'", (lot["lot_id"],)).fetchone()
        con.execute("UPDATE measurements SET attempt='remeasure', reason=?, operator='Kim' WHERE lot_id=? AND "
                    "property='viscosity' AND source='inhouse'",
                    ("First result over USL; same retained sample re-measured", lot["lot_id"]))
        con.execute("INSERT INTO measurements (lot_id, material, property, value, unit, source, instrument, profile, "
                    "operator, measured_at, source_file, attempt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lot["lot_id"], "product", "viscosity", 4640.0, row[1], "inhouse", row[2], row[3], "Park",
                     row[0], f"LIMS_first_result_{lot['lot_id']}", "initial"))
    return lot["lot_id"]


def seed_complaint(dbpath=None) -> str | None:
    """One customer complaint on the highest-viscosity lot of the last month (in spec, near USL)."""
    from . import qms
    p = db.product_wide(path=dbpath)
    if p.empty or "viscosity" not in p:
        return None
    lot = p.tail(20).sort_values("viscosity").iloc[-1]
    d = min((pd.to_datetime(lot["measured_at"]) + pd.Timedelta(days=4)).date(), date.today()).isoformat()
    return qms.add_complaint("Customer A (demo)", lot["lot_id"], "viscosity",
                             "Slow dispense flow, incomplete fillet on large die", 120, "kg", "major", d, dbpath)


if __name__ == "__main__":
    res = build()
    s = pd.Series([r.status for r in res]).value_counts()
    print(s.to_string())
