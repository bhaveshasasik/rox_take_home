"""Orchestration — what the routers call.

Everything here reads artifacts that already exist. Nothing triggers a Rox
fetch, and nothing writes to `Opportunity`: the existing scoring path stays
authoritative until the score diff has been reviewed.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger
from app.models import Account, OpportunityResearchLink, ResearchArtifact
from app.signals.elaboration import brief_sources, write_brief
from app.signals.extraction import extract_artifact
from app.signals.models import ArtifactExtraction, ExtractedSignalRow, ExtractionStatus
from app.signals.scoring import score_signals
from app.signals.schema import (
    AccountBriefOut,
    ExtractedSignal,
    ExtractedSignalOut,
    ExtractionHealthOut,
    ExtractionRunOut,
    ScoreBreakdownOut,
    ScoredAccount,
    SignalType,
    SourceRef,
    SourceRefOut,
    signal_label,
)

log = get_logger(__name__)

#: Signals per 1,000 characters below which an extraction is suspect. Picked
#: from the observed distribution rather than guessed: after the under-
#: extraction fixes, the lowest healthy extraction sits at 1.28 and the median
#: at 2.55, while the two known defects measured 0.23 and 0.49.
LOW_YIELD_PER_1K = 1.0

#: Short cells make the ratio meaningless — two signals in 200 characters is a
#: yield of 10, and one signal in 300 is a yield of 3.3. Neither says anything.
MIN_MEASURABLE_CHARS = 500

#: Enough to act on without turning the health payload into a report.
LOW_YIELD_SAMPLE = 10


def _to_signal(row: ExtractedSignalRow) -> ExtractedSignal:
    """ORM row -> the validated shape scoring and elaboration expect.

    `signal_type` is coerced through the enum with `OTHER` as the fallback:
    a row written under an older schema version, or by a model that slipped an
    unknown category through, must not crash the read path.
    """
    try:
        signal_type = SignalType(row.signal_type)
    except ValueError:
        signal_type = SignalType.OTHER

    return ExtractedSignal(
        signal_type=signal_type,
        confidence=max(0, min(10, row.confidence or 0)),
        is_absence=bool(row.is_absence),
        contested=bool(row.contested),
        rationale=row.rationale or "",
        evidence=row.evidence or "",
        sources=[SourceRef.model_validate(s) for s in (row.sources or [])],
    )


def _to_out(row: ExtractedSignalRow) -> ExtractedSignalOut:
    signal = _to_signal(row)
    return ExtractedSignalOut(
        id=row.id,
        signal_type=signal.signal_type.value,
        label=signal_label(signal.signal_type),
        confidence=signal.confidence,
        is_absence=signal.is_absence,
        contested=signal.contested,
        rationale=signal.rationale,
        evidence=signal.evidence,
        sources=[SourceRefOut(url=s.url, kind=s.kind) for s in signal.sources],
    )


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


async def extract_run(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> ExtractionRunOut:
    """Extract artifacts — one run's worth, or everything not yet done.

    Sequential on purpose. The per-account cell fetches in `research.py` are
    gathered concurrently because they are independent HTTP calls; these share
    one `AsyncSession`, which is not safe to use from concurrent tasks. The
    content-hash cache is what keeps the cost down, not parallelism.
    """
    settings = get_settings()

    query = (
        select(ResearchArtifact, Account.name)
        .join(Account, ResearchArtifact.account_id == Account.id)
        .where(ResearchArtifact.cell_value.is_not(None))
        .order_by(ResearchArtifact.fetched_at.desc())
    )
    if run_id:
        query = query.where(ResearchArtifact.run_id == run_id)
    if limit:
        query = query.limit(limit)

    rows = (await session.execute(query)).all()

    tally = {"extracted": 0, "reused": 0, "failed": 0, "skipped": 0}
    signals = 0

    for artifact, account_name in rows:
        record, outcome = await extract_artifact(
            session, artifact, account_name, force=force
        )
        tally[outcome] += 1
        if outcome in ("extracted", "reused"):
            signals += record.signal_count
        # Commit per artifact: a run over 264 cells is long enough that losing
        # everything to one failure at the end would be its own outage.
        await session.commit()

    log.info(
        "extraction run complete",
        examined=len(rows),
        schema_version=settings.extraction_schema_version,
        **tally,
    )
    return ExtractionRunOut(examined=len(rows), signals=signals, **tally)


async def extraction_health(session: AsyncSession) -> ExtractionHealthOut:
    """Coverage and failure rate, for `/admin/signals/health` and run health."""
    settings = get_settings()

    artifacts = (
        await session.execute(
            select(func.count(ResearchArtifact.id)).where(
                ResearchArtifact.cell_value.is_not(None)
            )
        )
    ).scalar_one()

    by_status = {
        status: count
        for status, count in (
            await session.execute(
                select(ArtifactExtraction.status, func.count(ArtifactExtraction.id))
                .group_by(ArtifactExtraction.status)
            )
        ).all()
    }
    by_version = {
        str(version): count
        for version, count in (
            await session.execute(
                select(
                    ArtifactExtraction.schema_version,
                    func.count(ArtifactExtraction.id),
                ).group_by(ArtifactExtraction.schema_version)
            )
        ).all()
    }
    by_signal_type = {
        signal_type: count
        for signal_type, count in (
            await session.execute(
                select(ExtractedSignalRow.signal_type, func.count(ExtractedSignalRow.id))
                .group_by(ExtractedSignalRow.signal_type)
                .order_by(func.count(ExtractedSignalRow.id).desc())
            )
        ).all()
    }

    total = sum(by_status.values())
    failed = by_status.get(ExtractionStatus.FAILED.value, 0)

    covered = (
        await session.execute(
            select(func.count(func.distinct(ArtifactExtraction.artifact_id))).where(
                ArtifactExtraction.schema_version == settings.extraction_schema_version
            )
        )
    ).scalar_one()

    # Yield, the tripwire for silent under-extraction. Measured against cell
    # length because the two real defects — a compound absence statement the
    # model skipped, and nested bullets it collapsed into one signal — both
    # scored normally and were invisible to every other metric here.
    yields = (
        await session.execute(
            select(
                Account.name,
                ArtifactExtraction.signal_count,
                func.length(ResearchArtifact.cell_value).label("chars"),
            )
            .join(ResearchArtifact, ArtifactExtraction.artifact_id == ResearchArtifact.id)
            .join(Account, ArtifactExtraction.account_id == Account.id)
            .where(
                ArtifactExtraction.status != ExtractionStatus.FAILED.value,
                func.length(ResearchArtifact.cell_value) >= MIN_MEASURABLE_CHARS,
            )
        )
    ).all()

    low: list[tuple[float, str]] = []
    signals_sum = chars_sum = 0
    for name, count, chars in yields:
        signals_sum += count or 0
        chars_sum += chars or 0
        rate = (count or 0) / (chars / 1000)
        if rate < LOW_YIELD_PER_1K:
            low.append((rate, name))
    low.sort()

    return ExtractionHealthOut(
        artifacts=artifacts,
        extractions=total,
        by_status=by_status,
        by_schema_version=by_version,
        by_signal_type=by_signal_type,
        failure_rate=round(failed / total, 4) if total else 0.0,
        unextracted=max(0, artifacts - covered),
        signals_per_1k_chars=(
            round(signals_sum / (chars_sum / 1000), 2) if chars_sum else 0.0
        ),
        low_yield=len(low),
        low_yield_accounts=[name for _rate, name in low[:LOW_YIELD_SAMPLE]],
    )


# --------------------------------------------------------------------------
# Reads, keyed by opportunity
# --------------------------------------------------------------------------


async def _signal_rows(
    session: AsyncSession, opportunity_id: str
) -> list[ExtractedSignalRow]:
    """Signal rows for an opportunity's linked research, current version only.

    Ordered strongest first, absences last — the same order the detail view
    renders and the brief reasons in, so the three never disagree.
    """
    settings = get_settings()
    return (
        (
            await session.execute(
                select(ExtractedSignalRow)
                .join(
                    ArtifactExtraction,
                    ExtractedSignalRow.extraction_id == ArtifactExtraction.id,
                )
                .join(
                    OpportunityResearchLink,
                    OpportunityResearchLink.artifact_id == ArtifactExtraction.artifact_id,
                )
                .where(
                    OpportunityResearchLink.opportunity_id == opportunity_id,
                    ArtifactExtraction.schema_version
                    == settings.extraction_schema_version,
                )
                # Ranking lives here rather than on the client: the tiebreak
                # needs `position` (the order the model emitted signals in),
                # which has no other use on the client, and two components read
                # this list — they must not be able to disagree about order.
                #
                # `id` closes the tiebreak. `position` is only unique within one
                # extraction, so an opportunity linked to two artifacts could
                # otherwise order differently between renders.
                .order_by(
                    ExtractedSignalRow.is_absence,
                    ExtractedSignalRow.confidence.desc(),
                    ExtractedSignalRow.position,
                    ExtractedSignalRow.id,
                )
            )
        )
        .scalars()
        .all()
    )


async def scored_for_artifact(
    session: AsyncSession, artifact_id: str
) -> tuple[ScoredAccount, list[ExtractedSignal]] | None:
    """Score one artifact from its stored signals, or None if not extracted.

    The seam opportunity creation scores through. None means "no extraction at
    this schema version" and is distinct from a score of 0 — the caller falls
    back to the legacy parser rather than treating the account as unqualified.
    """
    settings = get_settings()
    rows = (
        (
            await session.execute(
                select(ExtractedSignalRow)
                .join(
                    ArtifactExtraction,
                    ExtractedSignalRow.extraction_id == ArtifactExtraction.id,
                )
                .where(
                    ArtifactExtraction.artifact_id == artifact_id,
                    ArtifactExtraction.schema_version
                    == settings.extraction_schema_version,
                    ArtifactExtraction.status != ExtractionStatus.FAILED.value,
                )
                .order_by(ExtractedSignalRow.position)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    signals = [_to_signal(row) for row in rows]
    return score_signals(signals), signals


async def signals_for_opportunity(
    session: AsyncSession, opportunity_id: str
) -> list[ExtractedSignalOut]:
    return [_to_out(row) for row in await _signal_rows(session, opportunity_id)]


async def breakdown_for_opportunity(
    session: AsyncSession, opportunity_id: str
) -> ScoreBreakdownOut | None:
    """The 0-100 composite these signals would produce.

    Computed on read rather than stored on `Opportunity`: while the old path
    is still authoritative, a persisted column would be a second source of
    truth for the same number. Returns None when nothing has been extracted,
    which the UI reads as "not extracted yet" rather than "scored zero".
    """
    rows = await _signal_rows(session, opportunity_id)
    if not rows:
        return None

    scored = score_signals([_to_signal(row) for row in rows])
    return ScoreBreakdownOut.model_validate(scored.breakdown.model_dump())


async def sources_for_opportunity(
    session: AsyncSession, opportunity_id: str
) -> list[SourceRefOut]:
    signals = [_to_signal(row) for row in await _signal_rows(session, opportunity_id)]
    return [
        SourceRefOut(url=s.url, kind=s.kind) for s in brief_sources(signals)
    ]


async def brief_for_opportunity(
    session: AsyncSession, opportunity_id: str
) -> AccountBriefOut | None:
    """Elaborate this opportunity's stored signals into a brief.

    The only read path that calls an LLM, which is why it sits behind POST
    rather than being folded into the detail GET.
    """
    rows = await _signal_rows(session, opportunity_id)
    if not rows:
        return None

    account_name = (
        await session.execute(
            select(Account.name)
            .join(ResearchArtifact, ResearchArtifact.account_id == Account.id)
            .join(
                OpportunityResearchLink,
                OpportunityResearchLink.artifact_id == ResearchArtifact.id,
            )
            .where(OpportunityResearchLink.opportunity_id == opportunity_id)
            .limit(1)
        )
    ).scalar_one_or_none() or "Unknown account"

    signals = [_to_signal(row) for row in rows]
    brief = await write_brief(account_name, signals)
    if brief is None:
        return None

    return AccountBriefOut(
        headline=brief.headline,
        rationale=brief.rationale,
        why_now=brief.why_now,
        sources=[
            SourceRefOut(url=s.url, kind=s.kind) for s in brief_sources(signals)
        ],
        signal_count=len(signals),
    )
