"""Read-only tools the assistant may call. None of them can change data.

Each tool returns (result, source_id). The source id is what the answer cites, e.g. "lot:UF-2611-003".
Writing actions (release approval, closing an OOS investigation, ...) are deliberately not tools: they stay
with the people the procedures name.
"""
from __future__ import annotations

import json
import threading

from .. import config, db, engine, fdc, oos, outgoing
from ..insight import rules
from . import kb

_lock = threading.Lock()
_cache: dict = {}


def bundle(dbpath=None):
    key = (str(db.target(dbpath)), db.revision(dbpath))
    with _lock:
        if _cache.get("key") != key:
            _cache.update(key=key, bundle=engine.run(dbpath))
        return _cache["bundle"]


def _lots(dbpath):
    return set(bundle(dbpath).product["lot_id"])


def _not_found(lot_id: str):
    return {"error": f"lot {lot_id} not found", "known_lot_format": "UF-YYMM-NNN"}


# ---------------------------------------------------------------- tools

def get_lot_release(lot_id: str, lang: str = "ko", dbpath=None):
    b = bundle(dbpath)
    if lot_id not in _lots(dbpath):
        return _not_found(lot_id), f"lot:{lot_id}"
    level, why = outgoing.release_check(b, lot_id)
    row = b.product.set_index("lot_id").loc[lot_id]
    specs = config.specs()["product"]["properties"]
    vals = {rules.lab(p, lang): f"{float(row[p]):g} {specs[p]['unit']}" for p in specs if p in row and row[p] == row[p]}
    names = {"release": ("출하 가능", "release"), "hold": ("보류(QA 판단)", "hold (QA decision)"),
             "block": ("출하 불가", "blocked")}[level]
    return {"lot_id": lot_id, "status": names[0 if lang == "ko" else 1],
            "reasons": [r[0 if lang == "ko" else 1] for r in why], "reportable_values": vals}, f"lot:{lot_id}"


def get_test_history(lot_id: str, property: str | None = None, lang: str = "ko", dbpath=None):
    h = oos.history(lot_id, property, dbpath)
    if h.empty:
        return _not_found(lot_id), f"history:{lot_id}"
    names = oos.ATTEMPT_KO if lang == "ko" else oos.ATTEMPT_EN
    rows = [{"property": rules.lab(r.property, lang), "test": int(r.seq), "type": names.get(r.attempt, r.attempt),
             "value": float(r.value), "unit": r.unit, "out_of_spec": bool(r.oos), "invalidated": bool(r.invalidated),
             "oos_investigation": r.oos_no if isinstance(r.oos_no, str) else None,
             "analyst": r.operator, "reason": r.reason if isinstance(r.reason, str) else None}
            for r in h.itertuples()]
    return {"lot_id": lot_id, "results": rows}, f"history:{lot_id}"


def get_equipment_runs(lot_id: str, lang: str = "ko", dbpath=None):
    r = fdc.runs(lot_id, dbpath=dbpath)
    if r.empty:
        return {"lot_id": lot_id, "runs": [], "note": "no equipment data for this lot"}, f"equipment:{lot_id}"
    out = []
    for x in r.itertuples():
        d = fdc.run_detail(int(x.id), dbpath)
        out.append({"run": int(x.id), "equipment": x.equipment, "started": str(x.started)[:16], "status": x.status,
                    "alarms": [a["message_ko" if lang == "ko" else "message_en"] for a in d["alarms"].to_dict("records")]})
    return {"lot_id": lot_id, "runs": out}, f"equipment:{lot_id}"


def list_lots(status: str | None = None, limit: int = 40, lang: str = "ko", dbpath=None):
    st = outgoing.batch_status(bundle(dbpath), int(limit))
    if status in ("release", "hold", "block"):
        st = st[st["status"] == status]
    return {"filter": status or "all", "of_latest": int(limit), "count": int(len(st)),
            "lots": [{"lot_id": r.lot_id, "status": r.status,
                      "first_reason": (r.reasons[0][0 if lang == "ko" else 1] if r.reasons else "")}
                     for r in st.itertuples()]}, f"lots:{status or 'all'}"


def get_spec(property: str | None = None, lang: str = "ko", dbpath=None):
    sp = config.specs()["product"]["properties"]
    keys = [property] if property in sp else list(sp)
    return {rules.lab(p, lang): {"lsl": sp[p]["lsl"], "usl": sp[p]["usl"], "target": sp[p].get("target"),
                                  "unit": sp[p]["unit"]} for p in keys}, "SPEC-UF-300"


def list_open_oos(lang: str = "ko", dbpath=None):
    inv = oos.investigations(dbpath, open_only=True)
    return {"open_investigations": [{"no": r.no, "lot_id": r.lot_id, "property": rules.lab(r.property, lang),
                                     "value": float(r.value), "phase1": r.phase1} for r in inv.itertuples()]}, "oos:open"


def get_priority_findings(limit: int = 5, lang: str = "ko", dbpath=None):
    fs = rules.findings(bundle(dbpath))[: int(limit)]
    return {"findings": [{"severity": f.severity, "area": f.area, "title": f.title[lang],
                          "action": f.action[lang]} for f in fs]}, "findings"


def search_documents(query: str, k: int = 4, lang: str = "ko", dbpath=None):
    hits = kb.search(query, int(k))
    return {"query": query, "sections": [{"source": c.id, "title": c.title, "text": c.text} for c, _ in hits]}, \
        ",".join(c.id for c, _ in hits) or "documents:none"


# ---------------------------------------------------------------- registry (OpenAI tool schema)

def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


_LOT = {"lot_id": {"type": "string", "description": "Product lot, e.g. UF-2611-003"}}
_PROPS = list(config.specs()["product"]["properties"])

SCHEMAS = [
    _fn("get_lot_release", "Release decision (release / hold / blocked) of one product lot, with the reasons and the "
        "reportable test values. Use for any question about whether a lot can ship or why it is held.", _LOT, ["lot_id"]),
    _fn("get_test_history", "Every QC test result of a lot in test order, including first results that failed, "
        "retests and invalidated results.", {**_LOT, "property": {"type": "string", "enum": _PROPS}}, ["lot_id"]),
    _fn("get_equipment_runs", "Mixer runs that produced the lot, with FDC alarms (sensor disagreement, limits, step "
        "times).", _LOT, ["lot_id"]),
    _fn("list_lots", "Release status of the latest lots, optionally only one status.",
        {"status": {"type": "string", "enum": ["release", "hold", "block"]},
         "limit": {"type": "integer", "description": "how many latest lots (default 40)"}}),
    _fn("get_spec", "Product specification limits (LSL, USL, target, unit).",
        {"property": {"type": "string", "enum": _PROPS}}),
    _fn("list_open_oos", "Open OOS investigations."),
    _fn("get_priority_findings", "The prioritised quality findings shown on the overview page.",
        {"limit": {"type": "integer"}}),
    _fn("search_documents", "Search plant procedures (SOPs, work instructions, specification). Use for how-to, "
        "criteria, responsibilities and deadlines. The documents are in Korean: write the query in Korean.",
        {"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]),
]

TOOLS = {s["function"]["name"]: globals()[s["function"]["name"]] for s in SCHEMAS}


def call(name: str, args: dict, lang: str = "ko", dbpath=None) -> tuple[dict, str]:
    """Run a tool by name with model-provided arguments (unknown arguments are dropped)."""
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}, f"tool:{name}"
    fn = TOOLS[name]
    allowed = set(fn.__code__.co_varnames[: fn.__code__.co_argcount]) - {"lang", "dbpath"}
    clean = {k: v for k, v in (args or {}).items() if k in allowed and v not in (None, "")}
    try:
        return fn(**clean, lang=lang, dbpath=dbpath)
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}, f"tool:{name}"


def dumps(result: dict, limit: int = 3000) -> str:
    s = json.dumps(result, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…(truncated)"
