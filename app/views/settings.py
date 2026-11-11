"""Edit specs, rules and vocabulary in the app. Every save keeps the previous version."""
import shutil
from datetime import datetime

import streamlit as st
import yaml

import ui
from mqi import config

FILES = ["specs.yaml", "rules.yaml", "vocabulary.yaml"]
HIST = config.CONFIG_DIR / "history"


def render():
    st.title(ui.t("nav_settings"))
    name = st.segmented_control(ui.t("settings_file"), FILES, default=FILES[0], label_visibility="collapsed") or FILES[0]
    path = config.CONFIG_DIR / name
    left, right = st.columns([3, 1], gap="large")
    with left:
        text = st.text_area(" ", path.read_text(encoding="utf-8"), height=560, label_visibility="collapsed",
                            key=f"edit_{name}")
        if st.button(ui.t("save"), type="primary"):
            try:
                yaml.safe_load(text)
            except yaml.YAMLError as e:
                st.error(f"{ui.t('invalid')}: {e}")
            else:
                HIST.mkdir(exist_ok=True)
                shutil.copy(path, HIST / f"{path.stem}_{datetime.now():%Y%m%d-%H%M%S}.yaml")
                path.write_text(text, encoding="utf-8")
                config.reload()
                ui.clear()
                st.success(ui.t("saved"))
    with right:
        st.caption(ui.t("history"))
        versions = sorted(HIST.glob(f"{path.stem}_*.yaml"), reverse=True) if HIST.exists() else []
        if versions:
            v = st.radio(" ", versions, format_func=lambda p: p.stem.split("_", 1)[1], label_visibility="collapsed")
            if st.button(ui.t("restore")):
                shutil.copy(path, HIST / f"{path.stem}_{datetime.now():%Y%m%d-%H%M%S}.yaml")
                shutil.copy(v, path)
                config.reload()
                ui.clear()
                st.rerun()
        else:
            st.caption("—")
