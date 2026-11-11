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

    # ---- supplier CoA PDFs
    for _, r in t["resin"].iterrows():
        _coa_table(out, r, [("Epoxy Equivalent Weight", "g/eq", "180 - 195", r.eew + RNG.normal(0, 0.8)),
                            ("Viscosity @25°C", "mPa·s", "9000 - 14000", r.resin_visc + RNG.normal(0, 150))])
    for _, r in t["filler"].iterrows():
        if r.supplier == "NanoSphere":
            coa_d50 = 0.25 * r.d50 + 0.75 * 3.05 + RNG.normal(0, 0.03)  # CoA lags reality
            _coa_dotted(out, r, [("Mean particle size (D50)", f"{coa_d50:.2f}", "µm"),
                                 ("Moisture content", f"{(r.moisture + RNG.normal(0, 0.003)) * 1e4:.0f}", "ppm")])
        else:
            _coa_table(out, r, [("D50", "µm", "2.4 - 3.6", r.d50 + RNG.normal(0, 0.03)),
                                ("Moisture", "%", "max 0.10", r.moisture + RNG.normal(0, 0.003))])
    for _, r in t["hard"].iterrows():
        _coa_table(out, r, [("Amine Value", "mgKOH/g", "520 - 560", r.amine_value + RNG.normal(0, 1.2))])

    # ---- Gage R&R study
    rows = []
    parts = np.linspace(2900, 4150, 10) + RNG.normal(0, 60, 10)
    bias = {"Kim": 0, "Lee": 12, "Park": 72}
    for pi, true in enumerate(parts, 1):
        inter = {o: RNG.normal(0, 14) for o in OPS}
        for o in OPS:
            for trial in (1, 2, 3):
                rows.append(f"{pi},{o},{trial},{true + bias[o] + inter[o] + RNG.normal(0, 34):.0f}")
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


def _coa_header(c: canvas.Canvas, r, title: str) -> float:
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, 275 * mm, r.supplier)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, 269 * mm, f"Supplier: {r.supplier} Co., Ltd.")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, 255 * mm, title)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, 245 * mm, f"Lot No.: {r.lot_id}")
    c.drawString(110 * mm, 245 * mm, f"Manufactured: {r.made_at:%Y-%m-%d}")
    return 230 * mm


def _coa_table(out: Path, r, items) -> None:
    c = canvas.Canvas(str(out / f"CoA_{r.lot_id}.pdf"), pagesize=A4)
    y = _coa_header(c, r, "CERTIFICATE OF ANALYSIS")
    c.setFont("Helvetica-Bold", 10)
    for x, h in zip((20, 85, 120, 160), ("Test Item", "Unit", "Specification", "Result")):
        c.drawString(x * mm, y, h)
    c.setFont("Helvetica", 10)
    for name, unit, spec, val in items:
        y -= 8 * mm
        c.drawString(20 * mm, y, name)
        c.drawString(85 * mm, y, unit)
        c.drawString(120 * mm, y, spec)
        c.drawString(160 * mm, y, f"{val:.3g}" if abs(val) < 10 else f"{val:.1f}")
    c.drawString(20 * mm, y - 20 * mm, "Conclusion: PASS")
    c.save()


def _coa_dotted(out: Path, r, items) -> None:
    c = canvas.Canvas(str(out / f"CoA_{r.lot_id}.pdf"), pagesize=A4)
    y = _coa_header(c, r, "Quality Certificate")
    c.setFont("Courier", 10)
    for name, val, unit in items:
        y -= 9 * mm
        c.drawString(20 * mm, y, f"{name} {'.' * (40 - len(name))} {val} {unit}")
    c.drawString(20 * mm, y - 18 * mm, "Released by QA")
    c.save()


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
    return process_folder(folder, dbpath)


if __name__ == "__main__":
    res = build()
    s = pd.Series([r.status for r in res]).value_counts()
    print(s.to_string())
