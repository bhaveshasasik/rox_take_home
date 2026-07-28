"""Opportunity list, detail, and the accept/reject action."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal, get_session
from app.logging_config import get_logger
from app.models import (
    Account,
    DecisionType,
    Opportunity,
    OpportunityResearchLink,
    ResearchArtifact,
    Sequence,
)
from app.schemas import (
    DecisionIn,
    OpportunityDetailOut,
    OpportunityListOut,
    OpportunityOut,
    ResearchArtifactOut,
)
from app.services.decisions import AlreadyDecidedError, record_decision
from app.services.notifications import signal_label
from app.services.prospecting import run_prospecting

log = get_logger(__name__)
router = APIRouter(prefix="/opportunities", tags=["opportunities"])

SORT_FIELDS = {
    "created_at": Opportunity.created_at,
    "score": Opportunity.qualification_score,
    "decided_at": Opportunity.decided_at,
    "account": Account.name,
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
            fetched_at=a.fetched_at,
        )
        for a in artifacts
    ]
    detail.decision = opp.decision
    detail.notifications = opp.notifications
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
