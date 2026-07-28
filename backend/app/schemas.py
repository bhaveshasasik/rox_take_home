from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import DecisionType, ReasonCode


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------


class AccountOut(ORMModel):
    id: str
    rox_entity_id: str
    name: str
    domain: str | None = None
    industry: str | None = None
    parent_rox_entity_id: str | None = None
    synced_at: datetime


# ----------------------------------------------------------------------
# Research
# ----------------------------------------------------------------------


class ResearchColumnOut(ORMModel):
    id: str
    key: str
    name: str
    rox_column_id: str | None = None
    active: bool


class ResearchArtifactOut(ORMModel):
    id: str
    column_key: str | None = None
    column_name: str | None = None
    cell_value: str | None = None
    fetched_at: datetime


class ResearchRunOut(ORMModel):
    id: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    accounts_scanned: int
    cells_fetched: int
    cells_timed_out: int
    artifacts_created: int
    opportunities_created: int
    error: str | None = None


# ----------------------------------------------------------------------
# Opportunities
# ----------------------------------------------------------------------


class DecisionOut(ORMModel):
    id: str
    decision: str
    decided_by: str | None = None
    reason_code: str | None = None
    notes: str | None = None
    decided_at: datetime
    latency_seconds: float | None = None


class NotificationOut(ORMModel):
    id: str
    channel: str
    recipient: str | None = None
    status: str
    error: str | None = None
    attempts: int
    created_at: datetime
    sent_at: datetime | None = None


class OpportunityOut(ORMModel):
    id: str
    account_id: str
    account_name: str | None = None
    run_id: str | None = None
    title: str
    rationale: str
    qualification_score: int
    signal_type: str
    signal_label: str | None = None
    needs_review: bool
    status: str
    stage: str
    assigned_to: str | None = None
    created_at: datetime
    notified_at: datetime | None = None
    decided_at: datetime | None = None


class OpportunityDetailOut(OpportunityOut):
    research: list[ResearchArtifactOut] = Field(default_factory=list)
    decision: DecisionOut | None = None
    notifications: list[NotificationOut] = Field(default_factory=list)
    has_sequence: bool = False


class OpportunityListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[OpportunityOut]


class DecisionIn(BaseModel):
    decision: DecisionType
    decided_by: str | None = None
    reason_code: ReasonCode | None = None
    notes: str | None = None


# ----------------------------------------------------------------------
# Prospecting
# ----------------------------------------------------------------------


class OutreachEmailOut(ORMModel):
    id: str
    step_number: int
    subject: str
    body: str
    status: str
    send_at: datetime | None = None
    sent_at: datetime | None = None


class ContactOut(ORMModel):
    id: str
    name: str
    title: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    persona: str | None = None
    match_reason: str | None = None
    score: float | None = None


class EnrollmentOut(BaseModel):
    id: str
    status: str
    enrolled_at: datetime
    contact: ContactOut
    emails: list[OutreachEmailOut] = Field(default_factory=list)


class SequenceOut(BaseModel):
    id: str
    opportunity_id: str
    account_id: str
    account_name: str | None = None
    name: str
    status: str
    error: str | None = None
    created_at: datetime
    enrollments: list[EnrollmentOut] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Admin
# ----------------------------------------------------------------------


class RunTriggerOut(BaseModel):
    run: ResearchRunOut
    notified: int = 0


class MessageOut(BaseModel):
    message: str
    detail: Any | None = None


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------


class HeadlineOut(BaseModel):
    total_opportunities: int
    accepted: int
    rejected: int
    pending_review: int
    acceptance_rate: float


class FunnelStepOut(BaseModel):
    stage: str
    count: int
    pct_of_total: float


class FunnelOut(BaseModel):
    steps: list[FunnelStepOut]
    rejected: int


class ScoreBandOut(BaseModel):
    band: str
    decided: int
    accepted: int
    acceptance_rate: float


class ScoreCalibrationOut(BaseModel):
    total_decided: int
    bands: list[ScoreBandOut]


class SignalPerformanceOut(BaseModel):
    signal_type: str
    label: str
    created: int
    accepted: int
    rejected: int
    acceptance_rate: float


class DecisionLatencyOut(BaseModel):
    count: int
    median_hours: float | None = None
    p90_hours: float | None = None


class RejectionReasonOut(BaseModel):
    reason_code: str | None = None
    count: int
    pct: float


class RejectionReasonsOut(BaseModel):
    total_rejections: int
    reasons: list[RejectionReasonOut]


class UncoveredAccountOut(BaseModel):
    id: str
    name: str


class AccountCoverageOut(BaseModel):
    total_accounts: int
    accounts_with_opportunities: int
    coverage_rate: float
    uncovered_accounts: list[UncoveredAccountOut]


class ProspectingYieldOut(BaseModel):
    accepted_opportunities: int
    sequences_active: int
    activation_rate: float
    total_contacts: int
    total_emails_drafted: int
    avg_contacts_per_accepted: float


class RecentRunOut(BaseModel):
    id: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    accounts_scanned: int
    cells_fetched: int
    opportunities_created: int


class RunHealthOut(BaseModel):
    runs_considered: int
    success_rate: float
    total_cells_fetched: int
    recent_runs: list[RecentRunOut]


class JobTelemetryOut(BaseModel):
    lookback_hours: int
    total_jobs: int
    by_task_type: dict[str, dict[str, int]]
    avg_cell_generation_seconds: float | None = None


class OverviewOut(BaseModel):
    headline: HeadlineOut
    funnel: FunnelOut
    score_calibration: ScoreCalibrationOut
    signal_performance: list[SignalPerformanceOut]
    decision_latency: DecisionLatencyOut
    rejection_reasons: RejectionReasonsOut
    account_coverage: AccountCoverageOut
    prospecting_yield: ProspectingYieldOut
    run_health: RunHealthOut


# ----------------------------------------------------------------------
# Admin / health
# ----------------------------------------------------------------------


class DigestResultOut(BaseModel):
    sent: bool
    count: int
    recipient: str | None = None
    error: str | None = None


class HealthOut(BaseModel):
    status: str
    scheduler_running: bool
    next_research_run: str | None = None
    email_enabled: bool
    rox_token_configured: bool
