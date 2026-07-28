"""Manual triggers and notification management.

The manual research trigger matters for demoing: waiting 15 minutes for the
scheduler mid-demo is not viable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.logging_config import get_logger
from app.models import Notification, Opportunity
from app.rox.client import RoxClient
from app.schemas import (
    DigestResultOut,
    MessageOut,
    NotificationOut,
    ResearchRunOut,
    RunTriggerOut,
)
from app.services.notifications import notify_opportunity, retry_notification, send_digest
from app.services.research import run_research_cycle

log = get_logger(__name__)
router = APIRouter(tags=["admin"])


@router.post("/admin/research/run", response_model=RunTriggerOut)
async def trigger_research(
    notify: bool = True, session: AsyncSession = Depends(get_session)
) -> RunTriggerOut:
    """Run one research cycle now. Each opportunity notifies as it's created."""
    async with RoxClient() as rox:
        run = await run_research_cycle(session, rox, trigger="manual", notify=notify)

    notified = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.run_id == run.id, Opportunity.notified_at.is_not(None)
            )
        )
    ).scalars().all()

    return RunTriggerOut(run=ResearchRunOut.model_validate(run), notified=len(notified))


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    status: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[Notification]:
    query = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if status:
        query = query.where(Notification.status == status)
    return (await session.execute(query)).scalars().all()


@router.post("/notifications/{notification_id}/retry", response_model=NotificationOut)
async def retry(
    notification_id: str, session: AsyncSession = Depends(get_session)
) -> Notification:
    record = await retry_notification(session, notification_id)
    if record is None:
        raise HTTPException(404, "notification not found")
    return record


@router.post("/opportunities/{opportunity_id}/notify", response_model=NotificationOut)
async def notify_now(
    opportunity_id: str, session: AsyncSession = Depends(get_session)
) -> Notification:
    """Re-send the notification for one opportunity."""
    opp = (
        await session.execute(
            select(Opportunity)
            .where(Opportunity.id == opportunity_id)
            .options(selectinload(Opportunity.account))
        )
    ).scalar_one_or_none()
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    return await notify_opportunity(session, opp)


@router.post("/admin/notifications/digest", response_model=DigestResultOut)
async def trigger_digest(session: AsyncSession = Depends(get_session)) -> dict:
    """Email everything currently awaiting review as one digest."""
    return await send_digest(session)


@router.get("/admin/rox/me", response_model=MessageOut)
async def rox_me() -> MessageOut:
    """Connectivity check — confirms the token reaches Rox."""
    try:
        async with RoxClient() as rox:
            return MessageOut(message="ok", detail=await rox.get_me())
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        raise HTTPException(502, f"Rox unreachable: {exc}") from exc
