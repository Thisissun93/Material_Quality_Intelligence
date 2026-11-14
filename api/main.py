"""MQI REST API.

The same analysis and rules as the Streamlit app, exposed for other systems (MES, LIMS, equipment
collectors) and for any other front end. Run with:

    uvicorn api.main:app --reload            # SQLite file, or
    MQI_DATABASE_URL=postgresql://... uvicorn api.main:app

Interactive docs: /docs
"""
from __future__ import annotations

import threading
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from mqi import __version__, config, db, demo, engine, oos, outgoing
from mqi.insight import rules

from .schemas import (Finding, Health, HistoryRow, Investigation, LotStatus, OpenInvestigation, Phase1, Phase2,
                      Reason, ReleaseDetail, Retest)

app = FastAPI(
    title="Material Quality Intelligence API",
    version=__version__,
    description="Release decisions, test history and OOS investigations for an electronic-materials plant. "
                "Results are never edited: retests are added, and a failing result is set aside only by an "
                "OOS investigation that proves a laboratory error.",
)

# ---------------------------------------------------------------- analysis cache

_lock = threading.Lock()
_cache: dict = {}


def bundle():
    """Run the analysis once per data revision (about 2 s), like the Streamlit app does."""
    cfg = tuple(f.stat().st_mtime for f in config.CONFIG_DIR.glob("*.yaml"))
    key = (str(db.target()), db.revision(), cfg)
    with _lock:
        if _cache.get("key") != key:
            config.reload()
            _cache.update(key=key, bundle=engine.run())
        return _cache["bundle"]


def _clean(v):
    """NaN/None from pandas -> None (JSON null)."""
    return None if v is None or (isinstance(v, float) and v != v) else v


def _reasons(why) -> list[Reason]:
    return [Reason(ko=k, en=e) for k, e in why]


def _require_lot(b, lot_id: str) -> None:
    if lot_id not in set(b.product["lot_id"]):
        raise HTTPException(404, f"lot {lot_id} not found")


@app.exception_handler(ValueError)
async def _rule_violation(_: Request, exc: ValueError):
    """A business rule refused the request (e.g. retest before an OOS investigation).

    A response-model validation error is a bug on our side, not the caller's: 500.
    """
    if isinstance(exc, ValidationError):
        raise exc
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.middleware("http")
async def _timing(request: Request, call_next):
    t = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - t) * 1000:.0f}"
    return response


# ---------------------------------------------------------------- health

@app.get("/health", response_model=Health, tags=["system"])
def health():
    """Liveness and which store the service is using."""
    ok = db.exists()
    return Health(status="ok" if ok else "empty", backend=db.backend(), version=__version__,
                  demo_version=db.get_meta("demo_version") if ok else None, revision=str(db.revision()))


# ---------------------------------------------------------------- release

@app.get("/api/v1/lots", response_model=list[LotStatus], tags=["release"])
def lots(limit: int = Query(40, ge=1, le=500), status: str | None = Query(None, pattern="^(release|hold|block)$")):
    """Release status of the latest lots, newest first."""
    b = bundle()
    st = outgoing.batch_status(b, limit)
    if status:
        st = st[st["status"] == status]
    made = b.product.set_index("lot_id")["measured_at"]
    return [LotStatus(lot_id=r.lot_id, status=r.status, measured_at=str(made.get(r.lot_id, "")),
                      reasons=_reasons(r.reasons)) for r in st.itertuples()]


@app.get("/api/v1/lots/{lot_id}/release", response_model=ReleaseDetail, tags=["release"])
def release(lot_id: str):
    """Release decision for one lot with the reportable value of every specified property."""
    b = bundle()
    _require_lot(b, lot_id)
    level, why = outgoing.release_check(b, lot_id)
    row = b.product.set_index("lot_id").loc[lot_id]
    specs = config.specs()["product"]["properties"]
    values = {p: _clean(float(row[p])) for p in specs if p in row}
    return ReleaseDetail(lot_id=lot_id, status=level, reasons=_reasons(why), values=values,
                         specs={p: {k: specs[p].get(k) for k in ("lsl", "usl", "unit")} for p in specs})


@app.get("/api/v1/lots/{lot_id}/history", response_model=list[HistoryRow], tags=["test history"])
def history(lot_id: str, property: str | None = None):
    """Every in-house result of the lot in test order, including invalidated ones."""
    h = oos.history(lot_id, property)
    if h.empty:
        raise HTTPException(404, f"no results for lot {lot_id}")
    cols = ["id", "property", "seq", "attempt", "value", "unit", "oos", "invalidated", "oos_no", "operator",
            "measured_at", "reason"]
    return [HistoryRow(**{k: _clean(r[k]) for k in cols}) for r in h[cols].to_dict("records")]


@app.post("/api/v1/lots/{lot_id}/retests", status_code=201, tags=["test history"])
def add_retest(lot_id: str, body: Retest):
    """Add a repeat test next to the original result. Refused (409) while a failing result has no OOS
    investigation."""
    mid = oos.add_retest(lot_id, body.property, body.value, body.attempt, body.reason, body.operator,
                         body.instrument or "")
    return {"id": mid}


# ---------------------------------------------------------------- OOS

def _inv(no: str) -> Investigation:
    i = oos.investigation(no)
    if not i:
        raise HTTPException(404, f"investigation {no} not found")
    return Investigation(**{k: _clean(v) for k, v in i.items()})


@app.get("/api/v1/oos", response_model=list[Investigation], tags=["OOS"])
def investigations(open_only: bool = False):
    df = oos.investigations(open_only=open_only)
    return [Investigation(**{k: _clean(v) for k, v in r.items()}) for r in df.to_dict("records")]


@app.get("/api/v1/oos/{no}", response_model=Investigation, tags=["OOS"])
def investigation(no: str):
    return _inv(no)


@app.post("/api/v1/oos", response_model=Investigation, status_code=201, tags=["OOS"])
def open_investigation(body: OpenInvestigation):
    """Open an investigation on the first failing result of the lot/property that has none."""
    return _inv(oos.open_investigation(body.lot_id, body.property, body.opened_by))


@app.post("/api/v1/oos/{no}/phase1", response_model=Investigation, tags=["OOS"])
def phase1(no: str, body: Phase1):
    """Laboratory phase. A proven lab error (with category and evidence) invalidates the failing result."""
    _inv(no)
    oos.record_phase1(no, body.lab_error, body.cause, body.by, body.category)
    return _inv(no)


@app.post("/api/v1/oos/{no}/phase2", response_model=Investigation, tags=["OOS"])
def phase2(no: str, body: Phase2):
    """Manufacturing phase after no lab error was found: the OOS is confirmed with a disposition."""
    _inv(no)
    oos.record_phase2(no, body.finding, body.disposition, body.by)
    return _inv(no)


# ---------------------------------------------------------------- overview

@app.get("/api/v1/findings", response_model=list[Finding], tags=["overview"])
def findings():
    """The prioritised action list shown on the overview page."""
    return [Finding(key=f.key, area=f.area, severity=f.severity, title=f.title, evidence=f.evidence,
                    action=f.action) for f in rules.findings(bundle())]


@app.post("/api/v1/demo/rebuild", tags=["system"], include_in_schema=True)
def rebuild_demo():
    """Rebuild the synthetic demo plant (only allowed while the store holds demo data or nothing)."""
    if db.exists() and db.get_meta("demo_version") is None:
        raise HTTPException(403, "the store holds plant data, not demo data")
    demo.build()
    return {"demo_version": demo.DEMO_VERSION}
