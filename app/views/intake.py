import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import ui
from mqi import config, db
from mqi.ingest import pipeline
from mqi.instruments import registry

CATS = ["viscometer", "rheometer", "dsc", "tga", "particle_size", "moisture", "titrator", "other"]


def _status(s: str) -> str:
    return {"stored": "✅", "review": "🟡", "unknown": "🆕", "error": "⛔"}.get(s, s)


def _labels() -> dict:
    return {p["id"]: p.get("label", p["id"]) for p in registry.load_profiles()} | {"coa_reader": "CoA PDF reader"}


def _results_table(results):
    df = pd.DataFrame([{
        ui.t("status"): _status(r.status), ui.t("file"): r.file,
        ui.t("matched"): _labels().get(r.profile, r.profile or "-"), ui.t("confidence"): r.confidence,
        ui.t("alternatives"): ", ".join(f"{i} {c:.2f}" for i, c in (r.candidates or [])[1:3]),
        ui.t("rows"): r.rows} for r in results])
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={ui.t("confidence"): st.column_config.ProgressColumn(min_value=0, max_value=1,
                                                                                    format="%.2f")})


def render():
    st.title(ui.t("nav_intake"))
    tabs = st.tabs([ui.t("tab_results"), ui.t("tab_learn"), ui.t("tab_library"), ui.t("tab_log")])

    with tabs[0]:
        up = st.file_uploader(ui.t("upload"), accept_multiple_files=True, label_visibility="visible")
        st.caption(ui.t("watch_hint"))
        if up:
            folder = config.INBOX_DIR
            folder.mkdir(parents=True, exist_ok=True)
            results = []
            for f in up:
                p = folder / f.name
                p.write_bytes(f.getbuffer())
                results.append(pipeline.process_file(p))
            ui.clear()
            _results_table(results)
        else:
            log = db.read("SELECT file, kind, profile, confidence, rows, status FROM ingest_log ORDER BY id DESC")
            if not log.empty:
                s = log.groupby("status").size()
                c = st.columns(4)
                for i, k in enumerate(("stored", "review", "unknown", "error")):
                    c[i].metric(f"{_status(k)} {k}", int(s.get(k, 0)))
                by = log[log["status"] == "stored"].groupby("profile").agg(
                    files=("file", "count"), conf=("confidence", "mean")).reset_index()
                by["profile"] = by["profile"].map(lambda x: _labels().get(x, x))
                st.dataframe(by.rename(columns={"profile": ui.t("matched"), "files": ui.t("file"),
                                                "conf": ui.t("confidence")}),
                             hide_index=True, width="stretch",
                             column_config={ui.t("confidence"): st.column_config.ProgressColumn(
                                 min_value=0, max_value=1, format="%.2f")})

    with tabs[1]:
        _learn()

    with tabs[2]:
        profs = registry.load_profiles()
        lib = pd.DataFrame([{"id": p["id"], "label": p.get("label"), "category": p.get("category"),
                             "layout": p.get("layout"), "learned": bool(p.get("learned")),
                             "fields": ", ".join(k for k in p.get("columns", {}))} for p in profs])
        st.dataframe(lib, hide_index=True, width="stretch")

    with tabs[3]:
        st.dataframe(db.read("SELECT at, file, kind, profile, confidence, rows, status, note "
                             "FROM ingest_log ORDER BY id DESC"), hide_index=True, width="stretch")


def _learn():
    log = db.read("SELECT DISTINCT file, status FROM ingest_log WHERE status IN ('unknown','review') "
                  "AND file NOT IN (SELECT file FROM ingest_log WHERE status='stored')")
    candidates = []
    for name in log["file"]:
        for d in (config.INBOX_DIR, config.DATA_DIR / "demo_inbox"):
            if (d / name).exists():
                candidates.append(d / name)
                break
    if not candidates:
        st.success(ui.t("learn_none"))
        return
    pick = st.selectbox(ui.t("learn_pick"), candidates, format_func=lambda p: p.name)
    fp, matches = registry.rank(pick)
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.code("\n".join(fp.lines[:12]), language=None)
        st.dataframe(pd.DataFrame([{ui.t("matched"): m.id, ui.t("confidence"): m.confidence,
                                    **m.detail} for m in matches[:4]]),
                     hide_index=True, width="stretch")
    with right:
        draft = pd.DataFrame(registry.propose_mapping(fp))
        draft["unit"] = draft["unit"].fillna("")
        fields = [None] + list(registry._vocab()["fields"]) + list(config.all_property_specs())
        edited = st.data_editor(
            draft.rename(columns={"column": ui.t("column"), "field": ui.t("field"), "unit": ui.t("unit"),
                                  "confidence": ui.t("confidence")}),
            hide_index=True, width="stretch", disabled=[ui.t("column"), ui.t("confidence")],
            column_config={ui.t("field"): st.column_config.SelectboxColumn(options=fields),
                           ui.t("confidence"): st.column_config.ProgressColumn(min_value=0, max_value=1,
                                                                               format="%.2f")})
        name = st.text_input(ui.t("learn_name"), value=fp.lines[0].split(fp.delimiter or ":")[0][:40])
        cat = st.selectbox(ui.t("learn_cat"), CATS)
        if st.button(ui.t("learn_save"), type="primary"):
            mapping = edited.rename(columns={ui.t("column"): "column", ui.t("field"): "field",
                                             ui.t("unit"): "unit"}).to_dict("records")
            path = registry.save_profile(fp, mapping, name, cat)
            r = pipeline.process_file(pick)
            ui.clear()
            st.success(ui.t("learn_saved", p=Path(path).stem) + f"  ({r.status}, {r.rows})")
