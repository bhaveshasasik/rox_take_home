"""Requirement 1: the recurring schedule.

The job owns its own session (it runs outside any request). Notification
happens inside `run_research_cycle` itself, as each opportunity is created,
so the full research -> opportunity -> notify chain happens unattended.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import get_logger
from app.rox.client import RoxClient
from app.services.research import run_research_cycle

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None

RESEARCH_JOB_ID = "research_cycle"


async def scheduled_research_cycle() -> None:
    """One full unattended pass. Never raises — a failure must not kill the job."""
    try:
        async with SessionLocal() as session, RoxClient() as rox:
            run = await run_research_cycle(session, rox, trigger="scheduled")
            log.info(
                "scheduled cycle finished",
                opportunities=run.opportunities_created,
                status=run.status,
            )
    except Exception as exc:  # noqa: BLE001 - keep the scheduler alive
        log.error("scheduled research cycle failed", error=str(exc))


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        scheduled_research_cycle,
        trigger=IntervalTrigger(minutes=settings.research_interval_minutes),
        id=RESEARCH_JOB_ID,
        name="Periodic account research",
        replace_existing=True,
        # a slow run must not stack up behind itself
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info(
        "scheduler started", interval_minutes=settings.research_interval_minutes
    )
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler stopped")
    _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
