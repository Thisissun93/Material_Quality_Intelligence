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
    operator: str = Field(min_length=1)
    instrument: str | None = None


class OpenInvestigation(BaseModel):
    lot_id: str
    property: str
    opened_by: str = Field(min_length=1)


class Phase1(BaseModel):
    lab_error: bool
    cause: str = Field(min_length=1, description="Evidence of the laboratory check")
    by: str = Field(min_length=1)
    category: Literal["instrument", "analyst", "sample_prep", "calculation", "standard"] | None = None


class Phase2(BaseModel):
    finding: str = Field(min_length=1)
    disposition: Literal["scrap", "rework", "concession"]
    by: str = Field(min_length=1)


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
