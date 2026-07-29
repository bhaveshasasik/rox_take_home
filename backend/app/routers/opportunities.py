"""Opportunity list, detail, and the accept/reject action."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal, get_session
from app.logging_config import get_logger
from app.models import (
    Account,
    DecisionType,
    Opportunity,
    OpportunityResearchLink,
    OpportunityStatus,
    ResearchArtifact,
    Sequence,
    Stage,
    utcnow,
)
from app.schemas import (
    DecisionIn,
    DecisionOut,
    NotificationOut,
    OpportunityDetailOut,
    OpportunityListOut,
    OpportunityOut,
    PipelineStatsOut,
    ResearchArtifactOut,
    ResearchSignalOut,
)
from app.services.decisions import AlreadyDecidedError, record_decision
from app.services.notifications import signal_label
from app.services.parsing import parse_cell, parse_signals
from app.services.prospecting import run_prospecting

log = get_logger(__name__)
router = APIRouter(prefix="/opportunities", tags=["opportunities"])

#: Pipeline order, not alphabetical. Sorting `stage` as text yields
#: "accepted, notified, opportunity_created, ..." which tells a reviewer
#: nothing; these ordinals sort it the way the funnel actually runs.
#: `rejected` is terminal and off the acceptance path, so it sorts last.
_STAGE_ORDER = case(
    {
        Stage.RESEARCHED.value: 0,
        Stage.OPPORTUNITY_CREATED.value: 1,
        Stage.NOTIFIED.value: 2,
        Stage.ACCEPTED.value: 3,
        Stage.PROSPECTED.value: 4,
        Stage.SEQUENCED.value: 5,
        Stage.OUTREACH_SENT.value: 6,
        Stage.REJECTED.value: 7,
    },
    value=Opportunity.stage,
    else_=99,
)

#: Undecided first — the queue's whole job is surfacing what still needs action.
_STATUS_ORDER = case(
    {
        OpportunityStatus.NEW.value: 0,
        OpportunityStatus.ACCEPTED.value: 1,
        OpportunityStatus.REJECTED.value: 2,
    },
    value=Opportunity.status,
    else_=99,
)

SORT_FIELDS = {
    "created_at": Opportunity.created_at,
    "score": Opportunity.qualification_score,
    "decided_at": Opportunity.decided_at,
    "account": Account.name,
    "stage": _STAGE_ORDER,
    "status": _STATUS_ORDER,
    # no meaningful domain order — alphabetical matches the displayed label,
    # which is a title-cased rendering of this value
    "signal_type": Opportunity.signal_type,
}


def _to_out(opp: Opportunity) -> OpportunityOut:
    out = OpportunityOut.model_validate(opp)
    out.account_name = opp.account.name if opp.account else None
    out.signal_label = signal_label(opp.signal_type)
    return out


@router.get("", response_model=OpportunityListOut)
async def list_opportunities(
    session: AsyncSession = Depends(get_session),
    status: list[str] | None = Query(None, description="new | accepted | rejected"),
    stage: list[str] | None = Query(None),
    signal_type: list[str] | None = Query(None),
    account_id: str | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    max_score: int | None = Query(None, ge=0, le=100),
    needs_review: bool | None = None,
    search: str | None = Query(None, description="matches account name or title"),
    sort: str = Query("created_at", description=" | ".join(SORT_FIELDS)),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> OpportunityListOut:
    """Filterable pipeline view — the backing query for the opportunity list."""
    query = select(Opportunity).join(Account, Opportunity.account_id == Account.id)

    if status:
        query = query.where(Opportunity.status.in_(status))
    if stage:
        query = query.where(Opportunity.stage.in_(stage))
    if signal_type:
        query = query.where(Opportunity.signal_type.in_(signal_type))
    if account_id:
        query = query.where(Opportunity.account_id == account_id)
    if min_score is not None:
        query = query.where(Opportunity.qualification_score >= min_score)
    if max_score is not None:
        query = query.where(Opportunity.qualification_score <= max_score)
    if needs_review is not None:
        query = query.where(Opportunity.needs_review == needs_review)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Account.name.ilike(pattern) | Opportunity.title.ilike(pattern)
        )

    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()

    column = SORT_FIELDS.get(sort, Opportunity.created_at)
    query = query.order_by(column.asc() if order == "asc" else column.desc())
    query = query.options(selectinload(Opportunity.account)).limit(limit).offset(offset)

    rows = (await session.execute(query)).scalars().all()
    return OpportunityListOut(
        total=total, limit=limit, offset=offset, items=[_to_out(o) for o in rows]
    )


# Must stay ahead of `/{opportunity_id}`: FastAPI matches in declaration order,
# and the parameterised route would otherwise swallow "stats" as an id.
@router.get("/stats", response_model=PipelineStatsOut)
async def get_stats(
    session: AsyncSession = Depends(get_session),
    aging_hours: int = Query(
        48, ge=1, le=8760, description="age past which a pending opportunity is stale"
    ),
) -> PipelineStatsOut:
    """Counts for the pipeline header — how much is waiting, and how much has gone stale.

    Kept out of the list response because it is a property of the queue rather
    than of the current page, and computed here rather than in the browser: it
    spans every pending row, not just the ones that fit in one page.
    """
    pending_clause = Opportunity.status == OpportunityStatus.NEW.value
    cutoff = utcnow() - timedelta(hours=aging_hours)

    pending = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(pending_clause)
        )
    ).scalar_one()
    aging = (
        await session.execute(
            select(func.count())
            .select_from(Opportunity)
            .where(pending_clause, Opportunity.created_at < cutoff)
        )
    ).scalar_one()

    return PipelineStatsOut(
        pending=pending, aging=aging, aging_threshold_hours=aging_hours
    )


@router.get("/{opportunity_id}", response_model=OpportunityDetailOut)
async def get_opportunity(
    opportunity_id: str, session: AsyncSession = Depends(get_session)
) -> OpportunityDetailOut:
    opp = (
        await session.execute(
            select(Opportunity)
            .where(Opportunity.id == opportunity_id)
            .options(
                selectinload(Opportunity.account),
                selectinload(Opportunity.decision),
                selectinload(Opportunity.notifications),
            )
        )
    ).scalar_one_or_none()
    if opp is None:
        raise HTTPException(404, "opportunity not found")

    artifacts = (
        (
            await session.execute(
                select(ResearchArtifact)
                .join(
                    OpportunityResearchLink,
                    OpportunityResearchLink.artifact_id == ResearchArtifact.id,
                )
                .where(OpportunityResearchLink.opportunity_id == opportunity_id)
                .options(selectinload(ResearchArtifact.column))
            )
        )
        .scalars()
        .all()
    )

    has_sequence = (
        await session.execute(
            select(func.count(Sequence.id)).where(
                Sequence.opportunity_id == opportunity_id
            )
        )
    ).scalar_one() > 0

    detail = OpportunityDetailOut.model_validate(_to_out(opp).model_dump())
    detail.research = [
        ResearchArtifactOut(
            id=a.id,
            column_key=a.column.key if a.column else None,
            column_name=a.column.name if a.column else None,
            cell_value=a.cell_value,
            # parsed here rather than by the client: the raw cell is routinely
            # truncated mid-array and is not valid JSON
            signals=[
                ResearchSignalOut(
                    signal=s.signal, evidence=s.evidence, score=s.score
                )
                for s in parse_signals(a.cell_value)
            ],
            # Rox returns narrative prose for most cells; this is the readable
            # form of whichever shape came back.
            narrative=parse_cell(a.cell_value).rationale,
            fetched_at=a.fetched_at,
        )
        for a in artifacts
    ]
    # Validate rather than assigning the ORM rows straight in: pydantic does
    # not validate on assignment, so the raw model objects would sit in these
    # slots and get duck-typed at serialization time — which silently emits
    # plain strings where the schema now promises enums.
    detail.decision = (
        DecisionOut.model_validate(opp.decision) if opp.decision else None
    )
    detail.notifications = [
        NotificationOut.model_validate(n) for n in opp.notifications
    ]
    detail.has_sequence = has_sequence
    return detail


async def _prospect_in_background(opportunity_id: str) -> None:
    """Prospecting runs after the response is returned, on its own session."""
    async with SessionLocal() as session:
        opp = (
            await session.execute(
                select(Opportunity).where(Opportunity.id == opportunity_id)
            )
        ).scalar_one_or_none()
        if opp is None:
            return
        try:
            await run_prospecting(session, opp)
        except Exception as exc:  # noqa: BLE001 - background task must not crash silently
            log.error(
                "background prospecting failed",
                opportunity_id=opportunity_id,
                error=str(exc),
            )


@router.post("/{opportunity_id}/decision", response_model=OpportunityDetailOut)
async def decide(
    opportunity_id: str,
    payload: DecisionIn,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> OpportunityDetailOut:
    """Accept or reject. Accepting kicks off prospecting in the background."""
    opp = (
        await session.execute(
            select(Opportunity)
            .where(Opportunity.id == opportunity_id)
            .options(selectinload(Opportunity.account))
        )
    ).scalar_one_or_none()
    if opp is None:
        raise HTTPException(404, "opportunity not found")

    try:
        await record_decision(
            session,
            opp,
            decision=payload.decision,
            decided_by=payload.decided_by,
            reason_code=payload.reason_code.value if payload.reason_code else None,
            notes=payload.notes,
        )
    except AlreadyDecidedError as exc:
        raise HTTPException(409, str(exc)) from exc

    if payload.decision is DecisionType.ACCEPT:
        background.add_task(_prospect_in_background, opp.id)

    return await get_opportunity(opportunity_id, session)
