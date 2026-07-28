"""Accounts and research-run visibility."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Account, ResearchColumn, ResearchRun
from app.rox.client import RoxClient
from app.schemas import AccountOut, ResearchColumnOut, ResearchRunOut
from app.services.accounts import sync_accounts

router = APIRouter(tags=["accounts"])


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(session: AsyncSession = Depends(get_session)) -> list[Account]:
    return (
        (await session.execute(select(Account).order_by(Account.name))).scalars().all()
    )


@router.post("/accounts/sync", response_model=list[AccountOut])
async def sync(session: AsyncSession = Depends(get_session)) -> list[Account]:
    """Pull the 20 target accounts from the Rox customer hierarchy."""
    async with RoxClient() as rox:
        return await sync_accounts(session, rox)


@router.get("/research/columns", response_model=list[ResearchColumnOut])
async def list_columns(
    session: AsyncSession = Depends(get_session),
) -> list[ResearchColumn]:
    return (await session.execute(select(ResearchColumn))).scalars().all()


@router.get("/research/runs", response_model=list[ResearchRunOut])
async def list_runs(
    limit: int = 25, session: AsyncSession = Depends(get_session)
) -> list[ResearchRun]:
    return (
        (
            await session.execute(
                select(ResearchRun)
                .order_by(ResearchRun.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


@router.get("/research/runs/{run_id}", response_model=ResearchRunOut)
async def get_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> ResearchRun:
    run = (
        await session.execute(select(ResearchRun).where(ResearchRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    return run
