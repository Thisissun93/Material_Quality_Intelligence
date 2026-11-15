"""Request and response models of the REST API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["release", "hold", "block"]


class Reason(BaseModel):
    ko: str
    en: str


class Health(BaseModel):
    status: Literal["ok", "empty"]
    backend: Literal["sqlite", "postgresql"]
    version: str
    demo_version: str | None = None
    revision: str


class LotStatus(BaseModel):
    lot_id: str
    status: Status
    measured_at: str
    reasons: list[Reason]


class ReleaseDetail(BaseModel):
    lot_id: str
    status: Status
    reasons: list[Reason]
    values: dict[str, float | None] = Field(description="Reportable value per property (latest valid result)")
    specs: dict[str, dict]


class HistoryRow(BaseModel):
    id: int
    property: str
    seq: int = Field(description="1 = first test of this lot and property")
    attempt: str | None = Field(description="initial | remeasure | resample | null (repeat with no reason)")
    value: float
    unit: str | None
    oos: bool
    invalidated: bool
    oos_no: str | None
    operator: str | None
    measured_at: str | None
    reason: str | None


class Retest(BaseModel):
    property: str
    value: float
    attempt: Literal["remeasure", "resample"]
    reason: str = Field(min_length=1)
    operator: str | None = Field(None, description="Analyst who ran the test (default: the signed-in user)")
    instrument: str | None = None


class OpenInvestigation(BaseModel):
    lot_id: str
    property: str


class Phase1(BaseModel):
    lab_error: bool
    cause: str = Field(min_length=1, description="Evidence of the laboratory check")
    category: Literal["instrument", "analyst", "sample_prep", "calculation", "standard"] | None = None


class Phase2(BaseModel):
    finding: str = Field(min_length=1)
    disposition: Literal["scrap", "rework", "concession"]


class Investigation(BaseModel):
    id: int
    no: str
    lot_id: str
    property: str
    value: float | None
    source_file: str | None
    measured_at: str | None
    opened: str | None
    opened_by: str | None
    phase1: str | None
    phase1_category: str | None
    phase1_cause: str | None
    phase1_by: str | None
    phase1_at: str | None
    phase2_finding: str | None
    disposition: str | None
    phase2_by: str | None
    phase2_at: str | None
    outcome: str | None


class Finding(BaseModel):
    key: str
    area: str
    severity: str
    title: dict[str, str]
    evidence: dict[str, str]
    action: dict[str, str]


class TraceSample(BaseModel):
    t_s: float = Field(description="Process time since batch start (s)")
    step: str
    tag: str
    value: float


class EquipmentRunIn(BaseModel):
    lot_id: str = Field(min_length=1)
    equipment: str = Field(min_length=1)
    started: str | None = None
    ended: str | None = None
    source: str = "opcua"
    complete: bool = Field(True, description="False if the collector joined after the batch started")
    samples: list[TraceSample] = Field(min_length=1, description="Raw trace; the server resamples and judges it")


class FdcAlarm(BaseModel):
    step: str
    rule: str
    tag: str
    value: float | None
    lim: float | None
    message_ko: str
    message_en: str


class EquipmentRunResult(BaseModel):
    id: int
    status: Literal["ok", "alarm", "incomplete"]
    alarms: list[FdcAlarm]


class EquipmentRun(BaseModel):
    id: int
    lot_id: str
    equipment: str
    started: str | None
    ended: str | None
    source: str | None
    received: str | None
    samples: int | None
    status: str


class EquipmentRunDetail(EquipmentRun):
    summary: list[dict]
    alarms: list[FdcAlarm]
    trace: list[dict] | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    backend: Literal["offline", "llm", "llm-tools"] | None = Field(
        None, description="default: MQI_ASSISTANT_BACKEND (offline unless an on-site LLM is configured)")


class AskOut(BaseModel):
    question: str
    text: str
    backend: str
    lang: str
    sources: list[str]
    grounding: dict
    tool_calls: list[dict]
    latency_s: float
    error: str | None = None


# ---------------------------------------------------------------- users / release approval / audit

class LoginIn(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires: str
    username: str
    name: str
    role: str


class Me(BaseModel):
    username: str
    name: str
    role: str
    permissions: list[str]


class SignIn(BaseModel):
    password: str = Field(description="The signer's password again (electronic signature)")
    comment: str = Field("", description="Required for a hold lot and for a rejection")


class Decision(BaseModel):
    id: int
    action: Literal["review", "approve", "reject"]
    level: str
    comment: str | None
    evidence_hash: str
    actor: str
    actor_name: str | None
    role: str | None
    at: str
    meaning: str | None


class Approval(BaseModel):
    lot_id: str
    stage: Literal["none", "reviewed", "approved", "rejected"]
    stale: bool = Field(description="The lot's evidence changed after the last signature")
    coa_ready: bool = Field(description="Approved and the approval still covers the current evidence")
    level: str
    current_hash: str
    history: list[Decision]


class AuditEntry(BaseModel):
    id: int
    at: str
    actor: str
    role: str | None
    action: str
    target: str | None
    detail: dict
    hash: str


class AuditCheck(BaseModel):
    ok: bool
    rows: int
    broken_at: int | None
    reason: str
