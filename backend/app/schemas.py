from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    Channel,
    DecisionType,
    EmailStatus,
    EnrollmentStatus,
    NotificationStatus,
    OpportunityStatus,
    ReasonCode,
    RunStatus,
    SequenceStatus,
    Stage,
)
from app.signals.schema import (
    ExtractedSignalOut,
    ScoreBreakdownOut,
    SourceRefOut,
)


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


class ResearchSignalOut(BaseModel):
    """One signal recovered from a research cell."""

    #: the signal's own name, e.g. "Growth / Expansion"
    signal: str | None = None
    #: the supporting prose Rox returned for it
    evidence: str | None = None
    #: 0-100, same scale as `qualification_score`
    score: int | None = None


class ResearchArtifactOut(ORMModel):
    id: str
    column_key: str | None = None
    column_name: str | None = None
    #: The verbatim cell. Rox truncates long cells mid-array, so this is often
    #: invalid JSON — read `signals` instead of parsing it client-side.
    cell_value: str | None = None
    #: `cell_value` parsed server-side, strongest signal first. The headline
    #: `qualification_score` is the highest of these, so this is the breakdown
    #: behind the score rather than a separate metric. Empty when Rox returned
    #: narrative prose instead of structured signals, which is the majority
    #: of live cells — read `narrative` in that case.
    signals: list[ResearchSignalOut] = Field(default_factory=list)
    #: Readable research text, safe to render directly. Joined evidence when
    #: the cell was structured, the prose itself when it was not. Always use
    #: this or `signals` rather than `cell_value`.
    narrative: str | None = None
    fetched_at: datetime


class ResearchRunOut(ORMModel):
    id: str
    trigger: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    accounts_scanned: int
    cells_fetched: int
    cells_timed_out: int
    #: fetched but unscoreable — research that produced no opportunity
    cells_unscoreable: int
    artifacts_created: int
    opportunities_created: int
    error: str | None = None


# ----------------------------------------------------------------------
# Opportunities
# ----------------------------------------------------------------------


class DecisionOut(ORMModel):
    id: str
    decision: DecisionType
    decided_by: str | None = None
    reason_code: ReasonCode | None = None
    notes: str | None = None
    decided_at: datetime
    latency_seconds: float | None = None


class NotificationOut(ORMModel):
    id: str
    channel: Channel
    recipient: str | None = None
    status: NotificationStatus
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
    status: OpportunityStatus
    stage: Stage
    assigned_to: str | None = None
    created_at: datetime
    notified_at: datetime | None = None
    decided_at: datetime | None = None


class OpportunityDetailOut(OpportunityOut):
    research: list[ResearchArtifactOut] = Field(default_factory=list)
    decision: DecisionOut | None = None
    notifications: list[NotificationOut] = Field(default_factory=list)
    has_sequence: bool = False
    #: Structured signals from `app/signals`, when this opportunity's research
    #: has been extracted. Empty means not-yet-extracted, not no-signals — the
    #: regex-parsed `research[].signals` above is still the live path.
    extracted_signals: list[ExtractedSignalOut] = Field(default_factory=list)
    #: What the extracted signals would score, with per-factor contributions.
    #: Null until extraction runs; never null-because-zero.
    score_breakdown: ScoreBreakdownOut | None = None
    #: Deduped citations across the extracted signals, strongest first. The
    #: existing path strips these, so they appear only once extracted.
    sources: list[SourceRefOut] = Field(default_factory=list)


class OpportunityListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[OpportunityOut]


class PipelineStatsOut(BaseModel):
    """Queue health for the pipeline header.

    Deliberately independent of the caller's filters: this answers "how is the
    review queue doing", which stays true regardless of what the user is
    currently looking at. Filtered counts are already on `OpportunityListOut`.
    """

    #: opportunities still awaiting a decision
    pending: int
    #: of those, the ones that have gone stale — a queue should reveal its own neglect
    aging: int
    #: echoed back so the UI labels the number with the threshold that produced it
    aging_threshold_hours: int


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
    status: EmailStatus
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
    status: EnrollmentStatus
    enrolled_at: datetime
    contact: ContactOut
    emails: list[OutreachEmailOut] = Field(default_factory=list)


class SequenceOut(BaseModel):
    id: str
    opportunity_id: str
    account_id: str
    account_name: str | None = None
    name: str
    status: SequenceStatus
    error: str | None = None
    created_at: datetime
    enrollments: list[EnrollmentOut] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Admin
# ----------------------------------------------------------------------


class RunTriggerOut(BaseModel):
    run: ResearchRunOut
    notified: int = 0


class RoxIdentityOut(BaseModel):
    """Whoever the configured token authenticates as.

    Both fields are optional: this backs a connectivity check, and a reachable
    Rox that happens to omit a field should still report as reachable rather
    than failing response validation.
    """

    name: str | None = None
    email: str | None = None


class RoxMeOut(BaseModel):
    message: str
    detail: RoxIdentityOut | None = None


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
    stage: Stage
    count: int
    pct_of_total: float
    #: step-to-step conversion — this step's count over the *previous* step's.
    #: 100.0 on the first step, which has nothing to convert from. Null when
    #: the previous step is empty: converting out of zero is undefined, and
    #: reporting it as 0% next to a non-zero count reads as a collapse that
    #: did not happen.
    pct_of_previous: float | None = None


class FunnelOut(BaseModel):
    steps: list[FunnelStepOut]
    rejected: int


class ScoreBandOut(BaseModel):
    band: str
    #: inclusive numeric bounds, so charts can sort and position bands
    #: without parsing the `band` label
    lo: int
    hi: int
    decided: int
    accepted: int
    acceptance_rate: float


class ScoreCalibrationOut(BaseModel):
    total_decided: int
    #: number of buckets used; null when the default named bands are in effect
    buckets: int | None = None
    bands: list[ScoreBandOut]


class SignalPerformanceOut(BaseModel):
    signal_type: str
    label: str
    created: int
    accepted: int
    rejected: int
    acceptance_rate: float


class DecisionLatencyOut(BaseModel):
    #: every decision in the window, including ones we could not time
    count: int
    #: decisions that actually contributed to the percentiles below. When this
    #: is 0 the medians are null because there was nothing to measure — which
    #: is a different story from "no decisions yet", and the UI should say so.
    measured: int
    #: notified_at -> decided_at, the true review latency
    from_notification: int
    #: created_at -> decided_at, the fallback used when an opportunity was
    #: decided without ever being notified
    from_creation: int
    median_hours: float | None = None
    p90_hours: float | None = None


class RejectionReasonOut(BaseModel):
    #: null means the rejecting user did not supply a reason
    reason_code: ReasonCode | None = None
    count: int
    pct: float


class RejectionReasonPointOut(BaseModel):
    #: bucket start, `YYYY-MM-DD` for day / ISO `YYYY-Www` for week
    period: str
    reason_code: ReasonCode | None = None
    count: int


class RejectionReasonsOut(BaseModel):
    total_rejections: int
    reasons: list[RejectionReasonOut]
    #: bucket unit in effect; null when `group_by` was not requested
    group_by: str | None = None
    #: flat (period, reason_code, count) rows — empty unless `group_by` is set.
    #: Long form rather than wide so every field stays typed; pivot client-side.
    series: list[RejectionReasonPointOut] = Field(default_factory=list)


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
    #: comma-joined per-run overrides; a forced run's numbers are not a trend
    overrides: str | None = None
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    accounts_scanned: int
    cells_fetched: int
    cells_unscoreable: int
    opportunities_created: int


class RunHealthOut(BaseModel):
    runs_considered: int
    success_rate: float
    total_cells_fetched: int
    total_cells_unscoreable: int
    recent_runs: list[RecentRunOut]


class JobStateCountOut(BaseModel):
    #: Rox-side job state (COMPLETED, STOPPED, ...). Deliberately not an enum:
    #: Rox owns this vocabulary, and a closed union would fail validation the
    #: day they add a state.
    state: str
    count: int


class TaskTypeTelemetryOut(BaseModel):
    #: Rox-side task type (CUSTOM_CELL_GENERATION, ...) — open set, as above
    task_type: str
    total: int
    states: list[JobStateCountOut]


class JobTelemetryOut(BaseModel):
    lookback_hours: int
    total_jobs: int
    #: A list rather than a `{task_type: {state: count}}` record so both levels
    #: stay typed and the frontend can map over it without `Object.entries`.
    by_task_type: list[TaskTypeTelemetryOut]
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
    #: set when a Digest row was written, whatever the send outcome
    digest_id: str | None = None


class HealthOut(BaseModel):
    status: str
    scheduler_running: bool
    next_research_run: str | None = None
    email_enabled: bool
    rox_token_configured: bool
