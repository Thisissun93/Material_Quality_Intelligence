"""Route any dropped file to the right reader and store canonical records.

file ─┬─ .pdf ──────────────► CoA extractor ─────────────► measurements (source=coa)
      └─ anything else ─► rank all instrument profiles ─┬─ auto   ─► parse ─► measurements / lots / genealogy / msa
                                                        ├─ review ─► held for one-click confirmation
                                                        └─ unknown ► draft mapping proposed (learn once)
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .. import config, db
from ..instruments import registry
from ..instruments.parse import parse_table, parse_with
from . import coa


@dataclass
class Result:
    file: str
    kind: str
    status: str            # stored | review | unknown | error
    profile: str | None = None
    confidence: float | None = None
    rows: int = 0
    note: str = ""
    candidates: list | None = None


def _material(lot_ids: pd.Series, dbpath=None) -> pd.Series:
    known = db.read("SELECT lot_id, material FROM lots", (), dbpath).set_index("lot_id")["material"]
    prefix = config.specs().get("lot_prefix", {})
    return lot_ids.map(lambda l: known.get(l) or prefix.get(str(l).split("-")[0], "unknown"))


def _alias(df: pd.DataFrame) -> pd.DataFrame:
    alias = config.specs().get("material_alias", {})
    df["property"] = [alias.get(m, {}).get(p, p) for m, p in zip(df["material"], df["property"])]
    return df


def store_measurements(df: pd.DataFrame, source: str, file: str, dbpath=None) -> int:
    df = df.copy()
    df["material"] = _material(df["lot_id"], dbpath)
    df = _alias(df)
    df["source"] = source
    df["source_file"] = file
    return db.upsert_measurements(df, dbpath)


def store_profile_result(fp, prof: dict, file: str, dbpath=None) -> int:
    cat = prof.get("category")
    if cat == "master":
        t = parse_table(fp, prof)
        with db.connect(dbpath) as con:
            con.executemany("INSERT OR REPLACE INTO lots (lot_id, material, supplier, made_at) VALUES (?,?,?,?)",
                            t[["lot_id", "material", "supplier", "made_at"]].values.tolist())
        return len(t)
    if cat == "genealogy":
        t = parse_table(fp, prof).melt(id_vars="product_lot", var_name="material", value_name="raw_lot")
        with db.connect(dbpath) as con:
            con.executemany("INSERT OR REPLACE INTO genealogy VALUES (?,?,?)", t.values.tolist())
        return len(t)
    if cat == "msa":
        t = parse_table(fp, prof)
        prop = next(c for c in t.columns if c not in registry.META)
        t = t.rename(columns={prop: "value"})
        t["property"], t["instrument"], t["study"] = prop, "RotaVisc DV-2", Path(file).stem
        db.write_table("msa", t[["study", "property", "instrument", "part", "operator", "replicate", "value"]],
                       dbpath)
        return len(t)
    return store_measurements(parse_with(fp, prof), "inhouse", file, dbpath)


def process_file(path: Path | str, dbpath=None, profiles=None, archive: bool = False,
                 force_profile: dict | None = None) -> Result:
    path = Path(path)
    name = path.name
    try:
        if path.suffix.lower() == ".pdf":
            recs, info = coa.extract(path)
            if not info["lot_id"] or recs.empty:
                r = Result(name, "coa", "unknown", note="No lot or values found")
            else:
                if info.get("supplier"):
                    with db.connect(dbpath) as con:
                        con.execute("INSERT OR IGNORE INTO lots (lot_id, material, supplier, made_at) VALUES (?,?,?,?)",
                                    (info["lot_id"], _material(pd.Series([info["lot_id"]]), dbpath)[0],
                                     info["supplier"], None))
                n = store_measurements(recs, "coa", name, dbpath)
                r = Result(name, "coa", "stored", "coa_reader", 1.0, n)
        else:
            fp, matches = registry.rank(path, profiles)
            if force_profile is not None:
                status, top = "auto", registry.Match(force_profile, 1, 1)
            else:
                status, top = registry.decide(matches)
            cands = [(m.id, m.confidence) for m in matches[:3]]
            if status == "auto":
                n = store_profile_result(fp, top.profile, name, dbpath)
                r = Result(name, top.profile.get("category", "?"), "stored", top.id, top.confidence, n,
                           candidates=cands)
            else:
                r = Result(name, "instrument", status, top.id if top else None,
                           top.confidence if top else None, candidates=cands,
                           note="Needs confirmation" if status == "review" else "New format")
    except Exception as e:  # keep the watcher alive on any bad file
        r = Result(name, "?", "error", note=f"{type(e).__name__}: {e}")
    db.log_ingest(r.file, r.kind, r.profile, r.confidence, r.rows, r.status, r.note, dbpath)
    if archive and r.status == "stored":
        config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), config.ARCHIVE_DIR / name)
    return r


def _priority(p: Path) -> int:
    n = p.name.lower()
    return 0 if "lot_master" in n else 1 if "genealogy" in n else 2


def process_folder(folder: Path | str, dbpath=None, archive: bool = False) -> list[Result]:
    """Lot master first, then genealogy, then everything else (so materials resolve)."""
    files = sorted((p for p in Path(folder).iterdir() if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: (_priority(p), p.name))
    profiles = registry.load_profiles()
    return [process_file(p, dbpath, profiles, archive) for p in files]
