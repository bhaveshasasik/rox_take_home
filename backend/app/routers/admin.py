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
    NotificationOut,
    ResearchRunOut,
    RoxIdentityOut,
    RoxMeOut,
    RunTriggerOut,
)
from app.services.notifications import notify_opportunity, retry_notification, send_digest
from app.services.research import run_research_cycle
from app.signals.schema import ExtractionHealthOut, ExtractionRunOut
from app.signals.service import extract_run, extraction_health

log = get_logger(__name__)
router = APIRouter(tags=["admin"])


@router.post("/admin/research/run", response_model=RunTriggerOut)
async def trigger_research(
    notify: bool = True,
    force_extract: bool = False,
    ignore_cooldown: bool = False,
    session: AsyncSession = Depends(get_session),
) -> RunTriggerOut:
    """Run one research cycle now; qualifying opportunities notify in batch.

    Per-run overrides, recorded on the run row and never persisted anywhere
    else:
    - `force_extract` bypasses the settled-row skip in extract_artifact, so
      already-extracted artifacts re-extract. Known limit: it cannot bypass
      the content-hash twin lookup — an artifact whose text matches another's
      OK extraction copies rows instead of calling the model (~4% of cells),
      so force does not strictly mean "call the model".
    - `ignore_cooldown` skips the recently-decided cooldown for this run
      only. The open-opportunity guard still applies; superseding is the
      sanctioned way past it.
    """
    async with RoxClient() as rox:
        run = await run_research_cycle(
            session,
            rox,
            trigger="manual",
            notify=notify,
            force_extract=force_extract,
            ignore_cooldown=ignore_cooldown,
        )

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
    opportunity_id: str,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
) -> Notification:
    """Notify about one opportunity; re-sending requires `force=true`.

    Without the guard this endpoint notified the same opportunity repeatedly,
    inserting a fresh Notification row each time. A resend is recorded like
    any send but never overwrites the original `notified_at` — decision
    latency is measured from the first delivery.
    """
    opp = (
        await session.execute(
            select(Opportunity)
            .where(Opportunity.id == opportunity_id)
            .options(selectinload(Opportunity.account))
        )
    ).scalar_one_or_none()
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    if opp.notified_at is not None and not force:
        raise HTTPException(
            409,
            "already notified — pass force=true to re-send deliberately",
        )
    return await notify_opportunity(session, opp)


@router.post("/admin/notifications/digest", response_model=DigestResultOut)
async def trigger_digest(session: AsyncSession = Depends(get_session)) -> dict:
    """Email everything currently awaiting review as one digest."""
    return await send_digest(session)


@router.post("/admin/signals/extract", response_model=ExtractionRunOut)
async def extract_signals(
    run_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
) -> ExtractionRunOut:
    """Run structured extraction over stored research artifacts.

    Deliberately manual. Extraction does not run inside the research cycle
    yet, so this is the only thing that populates the signal tables — which
    keeps the LLM cost and the output itself reviewable before either touches
    the pipeline. Idempotent: artifacts already extracted at the current
    schema version are skipped unless `force` is set.
    """
    return await extract_run(session, run_id=run_id, limit=limit, force=force)


@router.get("/admin/signals/health", response_model=ExtractionHealthOut)
async def signals_health(
    session: AsyncSession = Depends(get_session),
) -> ExtractionHealthOut:
    """Extraction coverage, failure rate, and the signal-type distribution.

    The distribution is the one to watch: it is what
    `/reporting/signal-performance` will group on, and a collapse into
    `other` means the categories stopped matching what Rox returns.
    """
    return await extraction_health(session)


@router.get("/admin/rox/me", response_model=RoxMeOut)
async def rox_me() -> RoxMeOut:
    """Connectivity check — confirms the token reaches Rox."""
    try:
        async with RoxClient() as rox:
            identity = RoxIdentityOut.model_validate(await rox.get_me())
            return RoxMeOut(message="ok", detail=identity)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        raise HTTPException(502, f"Rox unreachable: {exc}") from exc
