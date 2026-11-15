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

import json

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import pandas as pd

from mqi import __version__, approval, audit, auth, config, db, demo, engine, fdc, oos, outgoing
from mqi.insight import rules

from .schemas import (Approval, AskIn, AskOut, AuditCheck, AuditEntry, Decision, LoginIn, Me, SignIn, Token,
                      EquipmentRun, EquipmentRunDetail, EquipmentRunIn, EquipmentRunResult, FdcAlarm, Finding, Health,
                      HistoryRow, Investigation, LotStatus, OpenInvestigation, Phase1, Phase2, Reason, ReleaseDetail,
                      Retest)

app = FastAPI(
    title="Material Quality Intelligence API",
    version=__version__,
    description="Release decisions, test history and OOS investigations for an electronic-materials plant. "
                "Results are never edited: retests are added, and a failing result is set aside only by an "
                "OOS investigation that proves a laboratory error. A lot is released by two electronic "
                "signatures (QA review, then the approver), each bound to a hash of the evidence it covered.\n\n"
                "Sign in with POST /api/v1/auth/token and send `Authorization: Bearer <token>`.",
)

# ---------------------------------------------------------------- users

_bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    u = auth.user_for_token(cred.credentials) if cred else None
    if not u:
        raise HTTPException(401, "sign in first (POST /api/v1/auth/token)", headers={"WWW-Authenticate": "Bearer"})
    return u


def need(perm: str):
    def dep(u: dict = Depends(current_user)) -> dict:
        if not auth.can(u, perm):
            raise HTTPException(403, f"role {u['role']} may not do this ({perm})")
        return u
    return dep

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


@app.exception_handler(approval.Denied)
async def _denied(_: Request, exc: approval.Denied):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


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


# ---------------------------------------------------------------- sign-in

@app.post("/api/v1/auth/token", response_model=Token, tags=["users"])
def login(body: LoginIn):
    """Exchange a user ID and password for a bearer token (valid 12 h). Failed attempts are audited."""
    u = auth.check_password(body.username, body.password)
    if not u:
        raise HTTPException(401, "wrong user ID or password")
    tok, exp = auth.issue_token(u)
    return Token(access_token=tok, expires=exp, **u)


@app.get("/api/v1/auth/me", response_model=Me, tags=["users"])
def me(u: dict = Depends(current_user)):
    return Me(**u, permissions=sorted(p for p in auth.PERMS if auth.can(u, p)))


# ---------------------------------------------------------------- health

@app.get("/health", response_model=Health, tags=["system"])
def health():
    """Liveness and which store the service is using."""
    ok = db.exists()
    return Health(status="ok" if ok else "empty", backend=db.backend(), version=__version__,
                  demo_version=db.get_meta("demo_version") if ok else None, revision=str(db.revision()))


# ---------------------------------------------------------------- release

@app.get("/api/v1/lots", response_model=list[LotStatus], tags=["release"])
def lots(limit: int = Query(40, ge=1, le=500), status: str | None = Query(None, pattern="^(release|hold|block)$"), _u: dict = Depends(need("read"))):
    """Release status of the latest lots, newest first."""
    b = bundle()
    st = outgoing.batch_status(b, limit)
    if status:
        st = st[st["status"] == status]
    made = b.product.set_index("lot_id")["measured_at"]
    return [LotStatus(lot_id=r.lot_id, status=r.status, measured_at=str(made.get(r.lot_id, "")),
                      reasons=_reasons(r.reasons)) for r in st.itertuples()]


@app.get("/api/v1/lots/{lot_id}/release", response_model=ReleaseDetail, tags=["release"])
def release(lot_id: str, _u: dict = Depends(need("read"))):
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
def history(lot_id: str, property: str | None = None, _u: dict = Depends(need("read"))):
    """Every in-house result of the lot in test order, including invalidated ones."""
    h = oos.history(lot_id, property)
    if h.empty:
        raise HTTPException(404, f"no results for lot {lot_id}")
    cols = ["id", "property", "seq", "attempt", "value", "unit", "oos", "invalidated", "oos_no", "operator",
            "measured_at", "reason"]
    return [HistoryRow(**{k: _clean(r[k]) for k in cols}) for r in h[cols].to_dict("records")]


@app.post("/api/v1/lots/{lot_id}/retests", status_code=201, tags=["test history"])
def add_retest(lot_id: str, body: Retest, u: dict = Depends(need("test.write"))):
    """Add a repeat test next to the original result. Refused (409) while a failing result has no OOS
    investigation. Role qc or qa."""
    mid = oos.add_retest(lot_id, body.property, body.value, body.attempt, body.reason, body.operator or u["name"],
                         body.instrument or "", actor=u["username"], role=u["role"])
    return {"id": mid}


# ---------------------------------------------------------------- OOS

def _inv(no: str) -> Investigation:
    i = oos.investigation(no)
    if not i:
        raise HTTPException(404, f"investigation {no} not found")
    return Investigation(**{k: _clean(v) for k, v in i.items()})


@app.get("/api/v1/oos", response_model=list[Investigation], tags=["OOS"])
def investigations(open_only: bool = False, _u: dict = Depends(need("read"))):
    df = oos.investigations(open_only=open_only)
    return [Investigation(**{k: _clean(v) for k, v in r.items()}) for r in df.to_dict("records")]


@app.get("/api/v1/oos/{no}", response_model=Investigation, tags=["OOS"])
def investigation(no: str, _u: dict = Depends(need("read"))):
    return _inv(no)


@app.post("/api/v1/oos", response_model=Investigation, status_code=201, tags=["OOS"])
def open_investigation(body: OpenInvestigation, u: dict = Depends(need("test.write"))):
    """Open an investigation on the first failing result of the lot/property that has none. Role qc or qa."""
    return _inv(oos.open_investigation(body.lot_id, body.property, u["name"], actor=u["username"], role=u["role"]))


@app.post("/api/v1/oos/{no}/phase1", response_model=Investigation, tags=["OOS"])
def phase1(no: str, body: Phase1, u: dict = Depends(need("test.write"))):
    """Laboratory phase. A proven lab error (with category and evidence) invalidates the failing result.
    Role qc or qa."""
    _inv(no)
    oos.record_phase1(no, body.lab_error, body.cause, u["name"], body.category, actor=u["username"], role=u["role"])
    return _inv(no)


@app.post("/api/v1/oos/{no}/phase2", response_model=Investigation, tags=["OOS"])
def phase2(no: str, body: Phase2, u: dict = Depends(need("oos.phase2"))):
    """Manufacturing phase after no lab error was found: the OOS is confirmed with a disposition. Role qa."""
    _inv(no)
    oos.record_phase2(no, body.finding, body.disposition, u["name"], actor=u["username"], role=u["role"])
    return _inv(no)


# ---------------------------------------------------------------- equipment / FDC

def _rec(d: dict) -> dict:
    return {k: _clean(v) for k, v in d.items()}


@app.post("/api/v1/equipment/runs", response_model=EquipmentRunResult, status_code=201, tags=["equipment"])
def post_run(body: EquipmentRunIn, u: dict = Depends(need("equipment.write"))):
    """Receive one equipment run (raw trace) from a collector. The server resamples it, summarises every recipe
    step, applies config/fdc.yaml and stores the result. An alarm puts the lot on QA hold. A run the collector
    joined half-way (complete=false) is stored as 'incomplete' and not judged."""
    trace = pd.DataFrame([s.model_dump() for s in body.samples])
    r = fdc.record_run(body.lot_id, body.equipment, trace, body.started, body.ended, body.source, body.complete,
                       actor=u["username"])
    return EquipmentRunResult(id=r["id"], status=r["status"], alarms=[FdcAlarm(**_rec(a)) for a in r["alarms"]])


@app.get("/api/v1/equipment/runs", response_model=list[EquipmentRun], tags=["equipment"])
def list_runs(lot_id: str | None = None, status: str | None = Query(None, pattern="^(ok|alarm|incomplete)$"),
              source: str | None = None, limit: int = Query(100, ge=1, le=1000), _u: dict = Depends(need("read"))):
    return [EquipmentRun(**_rec(r)) for r in fdc.runs(lot_id, status, source, limit).to_dict("records")]


@app.get("/api/v1/equipment/runs/{run_id}", response_model=EquipmentRunDetail, tags=["equipment"])
def get_run(run_id: int, trace: bool = False, _u: dict = Depends(need("read"))):
    d = fdc.run_detail(run_id)
    if not d:
        raise HTTPException(404, f"run {run_id} not found")
    return EquipmentRunDetail(**_rec(d["run"]),
                              summary=[_rec(r) for r in d["summary"].drop(columns="run_id").to_dict("records")],
                              alarms=[FdcAlarm(**_rec(a)) for a in d["alarms"].drop(columns="run_id").to_dict("records")],
                              trace=[_rec(r) for r in d["trace"].to_dict("records")] if trace else None)


# ---------------------------------------------------------------- assistant

@app.post("/api/v1/assistant/ask", response_model=AskOut, tags=["assistant"])
def ask(body: AskIn, _u: dict = Depends(need("read"))):
    """Ask the quality assistant. It looks up plant data with read-only tools and searches plant documents,
    answers with sources, and reports whether every number, lot and source in the answer is backed by what it
    looked up (grounding). It never changes data."""
    from mqi.assistant import agent

    a = agent.ask(body.question, body.backend)
    d = a.to_dict()
    d["tool_calls"] = [{"name": c["name"], "args": c["args"], "source": c["source"]} for c in d["tool_calls"]]
    return AskOut(**d)


# ---------------------------------------------------------------- release approval

def _approval(b, lot_id: str, st: dict) -> Approval:
    return Approval(lot_id=lot_id, stage=st["stage"], stale=st["stale"], coa_ready=st["coa_ready"], level=st["level"],
                    current_hash=st["current_hash"], history=[Decision(**r) for r in st["history"]])


@app.get("/api/v1/lots/{lot_id}/approval", response_model=Approval, tags=["release approval"])
def get_approval(lot_id: str, _u: dict = Depends(need("read"))):
    """Signatures on the lot and whether the latest one still covers the current evidence."""
    b = bundle()
    _require_lot(b, lot_id)
    return _approval(b, lot_id, approval.state(b, lot_id))


def _sign(fn, lot_id: str, body: SignIn, u: dict) -> Approval:
    b = bundle()
    _require_lot(b, lot_id)
    fn(b, lot_id, u, body.password, body.comment)
    _cache.clear()
    b = bundle()
    return _approval(b, lot_id, approval.state(b, lot_id))


@app.post("/api/v1/lots/{lot_id}/review", response_model=Approval, tags=["release approval"])
def review(lot_id: str, body: SignIn, u: dict = Depends(need("release.review"))):
    """QA review signature (role qa). Not for a blocked lot; a hold lot needs a comment."""
    return _sign(approval.review, lot_id, body, u)


@app.post("/api/v1/lots/{lot_id}/approve", response_model=Approval, tags=["release approval"])
def approve(lot_id: str, body: SignIn, u: dict = Depends(need("release.approve"))):
    """Release approval (role approver, not the reviewer). Refused if the evidence changed since the QA review."""
    return _sign(approval.approve, lot_id, body, u)


@app.post("/api/v1/lots/{lot_id}/reject", response_model=Approval, tags=["release approval"])
def reject(lot_id: str, body: SignIn, u: dict = Depends(need("release.approve"))):
    """Reject a reviewed lot (role approver). A comment is required."""
    return _sign(approval.reject, lot_id, body, u)


@app.get("/api/v1/release-decisions/{decision_id}/evidence", tags=["release approval"])
def decision_evidence(decision_id: int, _u: dict = Depends(need("read"))):
    """The evidence snapshot a signature was given, and a re-computed hash to compare with the stored one."""
    d = approval.decision(decision_id)
    if not d:
        raise HTTPException(404, f"decision {decision_id} not found")
    return {"decision_id": decision_id, "evidence_hash": d["evidence_hash"],
            "recomputed_hash": approval.digest(d["evidence"]), "evidence": d["evidence"]}


# ---------------------------------------------------------------- audit trail

@app.get("/api/v1/audit", response_model=list[AuditEntry], tags=["audit"])
def audit_entries(target: str | None = Query(None, description="e.g. lot/UF-2611-003"), action: str | None = None,
                  limit: int = Query(200, ge=1, le=5000), _u: dict = Depends(need("audit.read"))):
    """Who did what, newest first (roles qa, approver, admin)."""
    df = audit.entries(target, action, limit)
    return [AuditEntry(**{**{k: _clean(v) for k, v in r.items()}, "detail": json.loads(r["detail"] or "{}")})
            for r in df.to_dict("records")]


@app.get("/api/v1/audit/verify", response_model=AuditCheck, tags=["audit"])
def audit_verify(_u: dict = Depends(need("audit.read"))):
    """Re-compute the hash chain of the whole audit trail."""
    return AuditCheck(**audit.verify())


# ---------------------------------------------------------------- overview

@app.get("/api/v1/findings", response_model=list[Finding], tags=["overview"])
def findings(_u: dict = Depends(need("read"))):
    """The prioritised action list shown on the overview page."""
    return [Finding(key=f.key, area=f.area, severity=f.severity, title=f.title, evidence=f.evidence,
                    action=f.action) for f in rules.findings(bundle())]


@app.post("/api/v1/demo/rebuild", tags=["system"], include_in_schema=True)
def rebuild_demo(_u: dict = Depends(need("demo.rebuild"))):
    """Rebuild the synthetic demo plant (only allowed while the store holds demo data or nothing)."""
    if db.exists() and db.get_meta("demo_version") is None:
        raise HTTPException(403, "the store holds plant data, not demo data")
    demo.build()
    return {"demo_version": demo.DEMO_VERSION}
