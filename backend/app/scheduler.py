"""Requirement 1: the recurring schedule.

Once daily by default (`RESEARCH_RUN_AT`, cron), with the old interval
trigger surviving only as a fallback for calibration work. The job owns its
own session (it runs outside any request); the full research -> opportunity
-> notify chain happens unattended inside `run_research_cycle`.
"""

from __future__ import annotations

import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.logging_config import get_logger
from app.rox.client import RoxClient
from app.services.research import run_research_cycle

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None

RESEARCH_JOB_ID = "research_cycle"

#: How late a missed daily fire may still run. At daily cadence a skipped fire
#: means no pipeline that day, and APScheduler's default (`misfire_grace_time`
#: unset on the add_job level means the scheduler default of 1s effectively
#: skips anything it slept through) — so a server that was down at 07:00 and
#: back by early afternoon still runs. Past the grace, the day is lost and the
#: manual trigger is the recovery path.
MISFIRE_GRACE_SECONDS = 6 * 60 * 60

_RUN_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def research_trigger(settings: Settings) -> CronTrigger | IntervalTrigger:
    """The schedule: daily cron from `research_run_at`, or the legacy interval
    when it is blank.

    An invalid time raises rather than falling back — degrading from daily to
    every-15-minutes on a typo would be a 96x cost change nobody asked for,
    and a pipeline on the wrong cadence looks identical to a working one.
    """
    run_at = settings.research_run_at.strip()
    if not run_at:
        return IntervalTrigger(minutes=settings.research_interval_minutes)

    match = _RUN_AT.match(run_at)
    if match is None:
        raise ValueError(
            f"RESEARCH_RUN_AT={run_at!r} is not a valid HH:MM time; "
            "set a 24-hour UTC time like 07:00, or blank to fall back to "
            "RESEARCH_INTERVAL_MINUTES"
        )
    hour, minute = int(match.group(1)), int(match.group(2))
    return CronTrigger(hour=hour, minute=minute, timezone="UTC")


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


def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler
    settings = get_settings()

    if not settings.research_scheduler_enabled:
        # Logged at warning: an app silently not doing its recurring job looks
        # identical to one whose job is failing.
        log.warning("scheduler disabled by RESEARCH_SCHEDULER_ENABLED")
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    trigger = research_trigger(settings)

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        scheduled_research_cycle,
        trigger=trigger,
        id=RESEARCH_JOB_ID,
        name="Periodic account research",
        replace_existing=True,
        # a slow run must not stack up behind itself
        max_instances=1,
        # several missed fires collapse into one run, not a burst of catch-ups
        coalesce=True,
        # see MISFIRE_GRACE_SECONDS — a late fire beats a lost day
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    _scheduler.start()
    if isinstance(trigger, CronTrigger):
        log.info("scheduler started", daily_at_utc=settings.research_run_at)
    else:
        log.warning(
            "scheduler on interval fallback — calibration cadence, not production",
            interval_minutes=settings.research_interval_minutes,
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
