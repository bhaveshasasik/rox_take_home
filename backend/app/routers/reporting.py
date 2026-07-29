"""Reporting endpoints backing the metrics dashboard.

Every database-backed report takes the same optional `start`/`end` window,
half-open as `[start, end)`. Which timestamp each one filters on is documented
in `app.services.reporting`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.rox.client import RoxClient
from app.schemas import (
    AccountCoverageOut,
    DecisionLatencyOut,
    FunnelOut,
    JobTelemetryOut,
    OverviewOut,
    ProspectingYieldOut,
    RejectionReasonsOut,
    RunHealthOut,
    ScoreCalibrationOut,
    SignalPerformanceOut,
)
from app.services import reporting

router = APIRouter(prefix="/reporting", tags=["reporting"])

#: Shared across every report so the dashboard can pass one window to all of
#: them, and so the description is written once.
_START = Query(None, description="inclusive lower bound (ISO 8601)")
_END = Query(None, description="exclusive upper bound (ISO 8601)")


@dataclass
class Window:
    """Optional `[start, end)` reporting window."""

    start: datetime | None = None
    end: datetime | None = None


def get_window(
    start: datetime | None = _START,
    end: datetime | None = _END,
) -> Window:
    """Resolve and validate the window once, for every report.

    A function rather than a class dependency: this module uses postponed
    annotations, and FastAPI resolves forward refs against the callable's
    `__globals__`, which a class does not have.
    """
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must be before end")
    return Window(start, end)


@router.get("/overview", response_model=OverviewOut)
async def get_overview(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
    buckets: int | None = Query(
        None, ge=2, le=100, description="equal-width score buckets; 10 for deciles"
    ),
) -> dict:
    """Everything the dashboard needs in one round trip."""
    return await reporting.overview(session, window.start, window.end, buckets)


@router.get("/funnel", response_model=FunnelOut)
async def get_funnel(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
) -> dict:
    return await reporting.funnel(session, window.start, window.end)


@router.get("/score-calibration", response_model=ScoreCalibrationOut)
async def get_score_calibration(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
    buckets: int | None = Query(
        None,
        ge=2,
        le=100,
        description=(
            "equal-width score buckets; 10 for deciles. "
            "Omit for the default named bands (0-59, 60-74, 75-89, 90-100)."
        ),
    ),
) -> dict:
    """Acceptance rate by score band — is the qualification score predictive?"""
    return await reporting.score_calibration(
        session, window.start, window.end, buckets
    )


@router.get("/signal-performance", response_model=list[SignalPerformanceOut])
async def get_signal_performance(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
) -> list[dict]:
    return await reporting.signal_performance(session, window.start, window.end)


@router.get("/decision-latency", response_model=DecisionLatencyOut)
async def get_decision_latency(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
) -> dict:
    return await reporting.decision_latency(session, window.start, window.end)


@router.get("/rejection-reasons", response_model=RejectionReasonsOut)
async def get_rejection_reasons(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
    group_by: str | None = Query(
        None,
        pattern="^(day|week)$",
        description="bucket the `series` field by day or week; omit for totals only",
    ),
) -> dict:
    return await reporting.rejection_reasons(
        session, window.start, window.end, group_by
    )


@router.get("/account-coverage", response_model=AccountCoverageOut)
async def get_account_coverage(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
) -> dict:
    return await reporting.account_coverage(session, window.start, window.end)


@router.get("/prospecting-yield", response_model=ProspectingYieldOut)
async def get_prospecting_yield(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
) -> dict:
    return await reporting.prospecting_yield(session, window.start, window.end)


@router.get("/job-telemetry", response_model=JobTelemetryOut)
async def get_job_telemetry(
    lookback_hours: int = Query(24, ge=1, le=720, description="Rox-side lookback"),
) -> dict:
    """Live Rox task-queue health (task types, states, cell-generation time)."""
    async with RoxClient() as rox:
        return await reporting.job_telemetry(rox, lookback_hours=lookback_hours)


@router.get("/run-health", response_model=RunHealthOut)
async def get_run_health(
    session: AsyncSession = Depends(get_session),
    window: Window = Depends(get_window),
) -> dict:
    return await reporting.run_health(session, start=window.start, end=window.end)
