"""Reporting endpoints backing the metrics dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends
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


@router.get("/overview", response_model=OverviewOut)
async def get_overview(session: AsyncSession = Depends(get_session)) -> dict:
    """Everything the dashboard needs in one round trip."""
    return await reporting.overview(session)


@router.get("/funnel", response_model=FunnelOut)
async def get_funnel(session: AsyncSession = Depends(get_session)) -> dict:
    return await reporting.funnel(session)


@router.get("/score-calibration", response_model=ScoreCalibrationOut)
async def get_score_calibration(session: AsyncSession = Depends(get_session)) -> dict:
    """Acceptance rate by score band — is the qualification score predictive?"""
    return await reporting.score_calibration(session)


@router.get("/signal-performance", response_model=list[SignalPerformanceOut])
async def get_signal_performance(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await reporting.signal_performance(session)


@router.get("/decision-latency", response_model=DecisionLatencyOut)
async def get_decision_latency(session: AsyncSession = Depends(get_session)) -> dict:
    return await reporting.decision_latency(session)


@router.get("/rejection-reasons", response_model=RejectionReasonsOut)
async def get_rejection_reasons(session: AsyncSession = Depends(get_session)) -> dict:
    return await reporting.rejection_reasons(session)


@router.get("/account-coverage", response_model=AccountCoverageOut)
async def get_account_coverage(session: AsyncSession = Depends(get_session)) -> dict:
    return await reporting.account_coverage(session)


@router.get("/prospecting-yield", response_model=ProspectingYieldOut)
async def get_prospecting_yield(session: AsyncSession = Depends(get_session)) -> dict:
    return await reporting.prospecting_yield(session)


@router.get("/job-telemetry", response_model=JobTelemetryOut)
async def get_job_telemetry(lookback_hours: int = 24) -> dict:
    """Live Rox task-queue health (task types, states, cell-generation time)."""
    async with RoxClient() as rox:
        return await reporting.job_telemetry(rox, lookback_hours=lookback_hours)


@router.get("/run-health", response_model=RunHealthOut)
async def get_run_health(session: AsyncSession = Depends(get_session)) -> dict:
    return await reporting.run_health(session)
