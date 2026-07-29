"""The structured shape of one research cell, and what scoring makes of it.

Three model families live here:

- what the LLM is allowed to return (`SignalDraft`, `ExtractionResult`)
- what we persist after resolving sources (`ExtractedSignal`, `ExtractedResearch`)
- what scoring produces (`ScoredAccount` and its breakdown)

The split between the first two is deliberate and load-bearing: `SignalDraft`
has no source field at all, so there is no shape in which the model *can* hand
back a URL. See `extraction.resolve_sources` for why.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

#: Bump when the meaning of any field below changes. Persisted on every
#: extraction row so a schema change is a re-extraction rather than a
#: migration, and so rows written under two versions never get compared.
SCHEMA_VERSION = 1


class SignalType(str, Enum):
    """The six categories the research column's prompt actually asks for.

    Fixed, not open. The live `Opportunity.signal_type` column — slugified
    from whatever label Rox emitted — contains `trigger_event` and
    `trigger_events` as separate values, plus four entries that are entire
    sentences. Ten of twenty-one rows are unusable as a grouping key, which
    is what makes `/reporting/signal-performance` report roughly one bucket
    per account today.

    `OTHER` is the explicit dump for anything off-brief. Rox pads heavily
    with firmographics (headquarters, headcount, company website); because
    confidence measures *certainty* rather than strength, a maximum-confidence
    fact about an office address used to outscore every real buying signal.
    `OTHER` scores zero, which is the fix.
    """

    GROWTH_EXPANSION = "growth_expansion"
    BUYING_INTENT = "buying_intent"
    TRIGGER_EVENT = "trigger_event"
    CHAMPION_ENGAGEMENT = "champion_engagement"
    COMPETITIVE_DISPLACEMENT = "competitive_displacement"
    WHITESPACE_CROSS_SELL = "whitespace_cross_sell"
    OTHER = "other"


SIGNAL_LABELS: dict[str, str] = {
    SignalType.GROWTH_EXPANSION.value: "Growth / Expansion",
    SignalType.BUYING_INTENT.value: "Buying Intent",
    SignalType.TRIGGER_EVENT.value: "Trigger Event",
    SignalType.CHAMPION_ENGAGEMENT.value: "Champion / Engagement",
    SignalType.COMPETITIVE_DISPLACEMENT.value: "Competitive Displacement",
    SignalType.WHITESPACE_CROSS_SELL.value: "Whitespace / Cross-sell",
    SignalType.OTHER.value: "Other",
}

#: Types that can drive a score. Everything else is context.
SCORING_TYPES: frozenset[str] = frozenset(
    t.value for t in SignalType if t is not SignalType.OTHER
)


def signal_label(signal_type: str | SignalType) -> str:
    """Display label for a signal type.

    Falls back to the old title-cased rendering for the free-text values
    already in the database — those rows keep rendering until the backfill
    rewrites them, rather than showing a blank.
    """
    key = signal_type.value if isinstance(signal_type, SignalType) else str(signal_type or "")
    return SIGNAL_LABELS.get(key) or key.replace("_", " ").title()


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


class SourceKind(str, Enum):
    #: an external citation the reviewer can open
    WEB = "web"
    #: `rox://contact/<uuid>` — an internal CRM pointer. Kept for traceability,
    #: but it is not a link and must not be rendered as one.
    ROX = "rox"


class SourceRef(BaseModel):
    url: str
    kind: SourceKind = SourceKind.WEB


# --------------------------------------------------------------------------
# What the LLM returns — note the absence of any source field
# --------------------------------------------------------------------------


class SignalDraft(BaseModel):
    """One signal as the model reports it."""

    signal_type: SignalType
    #: 0-10, the scale Rox's own research emits. Deliberately not 0-100: an
    #: LLM has no real resolution at that granularity and the false precision
    #: degrades calibration. The 0-100 composite is built in `scoring.py`.
    confidence: int = Field(ge=0, le=10)
    #: The claim is that nothing was found. Stated explicitly rather than
    #: inferred, because this is the highest-consequence bit of the whole
    #: parse — high confidence in an *absence* is the weakest possible signal,
    #: and reading it as strength inflates the score to 100.
    is_absence: bool = False
    #: why this matters for this account
    rationale: str
    #: verbatim span from the source cell, for traceability
    evidence: str


class ExtractionResult(BaseModel):
    """The full LLM response for one cell."""

    signals: list[SignalDraft] = Field(default_factory=list)


# --------------------------------------------------------------------------
# What we persist — drafts plus sources resolved in code
# --------------------------------------------------------------------------


class ExtractedSignal(SignalDraft):
    sources: list[SourceRef] = Field(default_factory=list)
    #: evidence failed the verbatim-containment check and was discarded
    validation_failed: bool = False
    #: The model reported this as a positive finding, but its own evidence span
    #: asserts that nothing was found. Set in code, never by the model — see
    #: `extraction.contests_absence`. A contested signal cannot lift a score.
    contested: bool = False


class ExtractedResearch(BaseModel):
    signals: list[ExtractedSignal] = Field(default_factory=list)
    #: every source available on the artifact, whether or not a signal cited it
    sources: list[SourceRef] = Field(default_factory=list)
    schema_version: int = SCHEMA_VERSION


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


class ScoreFactor(BaseModel):
    name: str
    points: int
    detail: str


class ScoreBreakdown(BaseModel):
    total: int
    factors: list[ScoreFactor] = Field(default_factory=list)


class ScoredAccount(BaseModel):
    #: 0-100, same scale as `Opportunity.qualification_score`
    score: int
    #: the signal that set the base, or None when nothing scoreable was found
    signal_type: SignalType | None = None
    needs_review: bool = False
    breakdown: ScoreBreakdown


# --------------------------------------------------------------------------
# API output
# --------------------------------------------------------------------------


class SourceRefOut(BaseModel):
    url: str
    kind: SourceKind


class ExtractedSignalOut(BaseModel):
    id: str
    signal_type: str
    label: str
    confidence: int
    is_absence: bool
    #: the evidence contradicts `is_absence`; shown as disputed, not as signal
    contested: bool = False
    #: evidence could not be located in the source cell and was discarded
    validation_failed: bool = False
    rationale: str
    evidence: str
    sources: list[SourceRefOut] = Field(default_factory=list)


class ScoreFactorOut(BaseModel):
    name: str
    points: int
    detail: str


class ScoreBreakdownOut(BaseModel):
    total: int
    factors: list[ScoreFactorOut] = Field(default_factory=list)


class AccountBriefOut(BaseModel):
    headline: str
    rationale: str
    why_now: str
    #: sources backing the signals the brief was written from
    sources: list[SourceRefOut] = Field(default_factory=list)
    signal_count: int = 0


class ExtractionRunOut(BaseModel):
    examined: int
    #: cells sent to the model
    extracted: int
    #: cells served from an identical earlier extraction
    reused: int
    failed: int
    #: already extracted at this schema version
    skipped: int
    signals: int


class ExtractionHealthOut(BaseModel):
    artifacts: int
    extractions: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_schema_version: dict[str, int] = Field(default_factory=dict)
    by_signal_type: dict[str, int] = Field(default_factory=dict)
    failure_rate: float = 0.0
    #: artifacts with no extraction row at the current schema version
    unextracted: int = 0
    #: Signals recovered per 1,000 characters of cell text. The tripwire for
    #: under-extraction, which is otherwise silent — a cell that yields nothing
    #: is indistinguishable from an account that genuinely has no signals.
    #: Observed healthy range is 1.3-7.3, median ~2.5; the two known defects
    #: sat at 0.23 and 0.49.
    signals_per_1k_chars: float = 0.0
    #: extractions below the yield floor, ignoring cells too short to measure
    low_yield: int = 0
    #: worst offenders by name, so the number is actionable rather than alarming
    low_yield_accounts: list[str] = Field(default_factory=list)
