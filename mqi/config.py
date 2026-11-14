"""Single place that loads YAML settings. Edit YAML, not code."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
INSTRUMENT_DIR = CONFIG_DIR / "instruments"
DATA_DIR = ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"
ARCHIVE_DIR = DATA_DIR / "archive"
DB_PATH = DATA_DIR / "mqi.sqlite"


def database_url() -> str | None:
    """PostgreSQL URL from MQI_DATABASE_URL (e.g. postgresql://mqi:mqi@db:5432/mqi). None = SQLite file."""
    return os.environ.get("MQI_DATABASE_URL") or None


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=None)
def specs() -> dict:
    return _load("specs.yaml")


@lru_cache(maxsize=None)
def rules() -> dict:
    return _load("rules.yaml")


def all_property_specs() -> dict[str, dict]:
    """Flat map: property -> spec dict (product and raw materials)."""
    s = specs()
    out = {k: {**v, "scope": "product"} for k, v in s["product"]["properties"].items()}
    for mat, m in s["raw_materials"].items():
        for k, v in m["properties"].items():
            out[k] = {**v, "scope": mat}
    return out


def plausible_range(prop: str) -> tuple[float, float] | None:
    r = specs().get("plausible", {}).get(prop)
    return tuple(r) if r else None


def reload() -> None:
    specs.cache_clear()
    rules.cache_clear()
