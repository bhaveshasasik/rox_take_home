"""Prospecting view: contacts, sequence status, and the generated emails."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Account, Opportunity, OpportunityStatus, Sequence
from app.schemas import ContactOut, EnrollmentOut, OutreachEmailOut, SequenceOut
from app.services.prospecting import run_prospecting

router = APIRouter(prefix="/prospecting", tags=["prospecting"])

def _to_out(sequence: Sequence, account_name: str | None) -> SequenceOut:
    return SequenceOut(
        id=sequence.id,
        opportunity_id=sequence.opportunity_id,
        account_id=sequence.account_id,
        account_name=account_name,
        name=sequence.name,
        status=sequence.status,
        error=sequence.error,
        created_at=sequence.created_at,
        enrollments=[
            EnrollmentOut(
                id=e.id,
                status=e.status,
                enrolled_at=e.enrolled_at,
                contact=ContactOut.model_validate(e.contact),
                emails=[
                    OutreachEmailOut.model_validate(m)
                    for m in sorted(e.emails, key=lambda m: m.step_number)
                ],
            )
            for e in sequence.enrollments
        ],
    )


def _with_relations(query):
    from app.models import SequenceEnrollment

    return query.options(
        selectinload(Sequence.enrollments).selectinload(SequenceEnrollment.contact),
        selectinload(Sequence.enrollments).selectinload(SequenceEnrollment.emails),
    )


@router.get("/sequences", response_model=list[SequenceOut])
async def list_sequences(
    session: AsyncSession = Depends(get_session),
) -> list[SequenceOut]:
    sequences = (
        (await session.execute(_with_relations(select(Sequence).order_by(Sequence.created_at.desc()))))
        .scalars()
        .unique()
        .all()
    )
    names = {
        a.id: a.name for a in (await session.execute(select(Account))).scalars().all()
    }
    return [_to_out(s, names.get(s.account_id)) for s in sequences]


@router.get("/opportunities/{opportunity_id}", response_model=SequenceOut)
async def get_sequence_for_opportunity(
    opportunity_id: str, session: AsyncSession = Depends(get_session)
) -> SequenceOut:
    sequence = (
        await session.execute(
            _with_relations(
                select(Sequence).where(Sequence.opportunity_id == opportunity_id)
            )
        )
    ).scalars().unique().one_or_none()
    if sequence is None:
        raise HTTPException(404, "no prospecting has run for this opportunity")

    account = (
        await session.execute(select(Account).where(Account.id == sequence.account_id))
    ).scalar_one_or_none()
    return _to_out(sequence, account.name if account else None)


@router.post("/opportunities/{opportunity_id}/run", response_model=SequenceOut)
async def rerun_prospecting(
    opportunity_id: str, session: AsyncSession = Depends(get_session)
) -> SequenceOut:
    """Manually trigger prospecting — used to retry after a failure."""
    opp = (
        await session.execute(
            select(Opportunity).where(Opportunity.id == opportunity_id)
        )
    ).scalar_one_or_none()
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    if opp.status != OpportunityStatus.ACCEPTED.value:
        raise HTTPException(
            409, "prospecting only runs for accepted opportunities"
        )

    sequence = await run_prospecting(session, opp)
    if sequence is None:
        raise HTTPException(500, "prospecting could not start")

    return await get_sequence_for_opportunity(opportunity_id, session)
