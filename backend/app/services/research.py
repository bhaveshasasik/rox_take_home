"""Requirement 1: periodic research.

One cycle, per configured column:

    resolve column_id by name   (/account_research/account_research_section)
    refresh_by_tab(col, org_id) (trigger regeneration)
    poll /priority_jobs         (wait for CUSTOM_CELL_GENERATION children)
    per-account read            (/research/clever_column/{col}/cell/{entity})

Reads go per-account rather than through the bulk endpoint because bulk only
returns the ~300-char-capped `cell_value`; the per-entity endpoint also
returns `output.text` — the full, untruncated research narrative — and
`output.sources`, which opportunities.py needs for LLM rationale expansion.
Fetches are fanned out with a bounded semaphore (`research_concurrency`).

Two facts drive the design:
  - Columns created via the API never generate cells, so we read UI-authored
    columns and trigger them with refresh_by_tab.
  - refresh_by_tab returns 201 "Successfully triggered" for any UUID, so the
    only proof that work happened is a CUSTOM_CELL_GENERATION job appearing in
    /priority_jobs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger
from app.models import (
    Account,
    ResearchArtifact,
    ResearchColumn,
    ResearchRun,
    RunStatus,
    utcnow,
)
from app.rox.client import RoxClient, normalize_uuid, unwrap
from app.services.accounts import sync_accounts
from app.services.research_columns import ColumnRef, active_column_refs

log = get_logger(__name__)

CELL_JOB = "CUSTOM_CELL_GENERATION"
COLUMN_JOB = "CUSTOM_COLUMN_GENERATION"
DONE_STATES = {"COMPLETED", "FAILED", "SKIPPED"}


# ----------------------------------------------------------------------
# Column resolution
# ----------------------------------------------------------------------


async def resolve_columns(
    session: AsyncSession, rox: RoxClient, refs: list[ColumnRef] | None = None
) -> list[tuple[ResearchColumn, ColumnRef]]:
    """Map configured column names to live Rox column ids.

    Resolved by name rather than hardcoded UUID so the same registry works
    across orgs. Sections repeat by name, so we dedupe on column_id and verify
    the column is actually readable — roughly 30% of them 403 with "Section
    associated with this column is not available for the user".
    """
    refs = refs if refs is not None else active_column_refs()

    sections = unwrap(await rox.list_account_research_sections())
    if not isinstance(sections, list):
        sections = []

    by_name: dict[str, dict] = {}
    for section in sections:
        if not isinstance(section, dict) or not section.get("column_id"):
            continue
        by_name.setdefault(str(section.get("name", "")).strip().lower(), section)

    existing = {
        c.key: c for c in (await session.execute(select(ResearchColumn))).scalars().all()
    }

    resolved: list[tuple[ResearchColumn, ColumnRef]] = []
    for ref in refs:
        section = by_name.get(ref.rox_name.strip().lower())
        if section is None:
            message = f"column {ref.rox_name!r} not found in this org"
            if ref.required:
                raise RuntimeError(message)
            log.warning("skipping column", column=ref.rox_name, reason="not found")
            continue

        column_id = str(section["column_id"])

        # Readability check: a 403 here means the section belongs to another
        # user, and every later read of it would fail silently.
        try:
            await rox.get_customers_paginated(column_id)
        except Exception as exc:  # noqa: BLE001 - degrade this column only
            if ref.required:
                raise RuntimeError(f"required column {ref.rox_name!r} unreadable: {exc}") from exc
            log.warning("skipping column", column=ref.rox_name, reason=str(exc)[:200])
            continue

        column = existing.get(ref.key)
        if column is None:
            column = ResearchColumn(key=ref.key, name=ref.rox_name)
            session.add(column)
        column.name = ref.rox_name
        column.rox_column_id = column_id
        column.rox_section_id = str(section.get("id") or "")
        column.active = True
        resolved.append((column, ref))

    # A column dropped from the registry (not merely unreadable this run)
    # should stop reading as "active" — otherwise `/research/columns` keeps
    # advertising columns nothing reads anymore.
    ref_keys = {ref.key for ref in refs}
    for key, column in existing.items():
        if key not in ref_keys and column.active:
            column.active = False
            log.info("column deactivated, no longer in registry", key=key)

    await session.commit()
    log.info("columns resolved", count=len(resolved), keys=[c.key for c, _ in resolved])
    return resolved


# ----------------------------------------------------------------------
# Regeneration
# ----------------------------------------------------------------------


async def trigger_refresh(
    rox: RoxClient, column_id: str, org_id: str
) -> tuple[list[str], int]:
    """Trigger regeneration and identify the jobs it actually created.

    Returns (child_run_ids, enqueued). An empty list means the refresh was
    accepted but scheduled nothing — the API-created-column failure mode.
    """
    settings = get_settings()
    before = {j["run_id"] for j in await rox.list_priority_jobs() if j.get("run_id")}
    await rox.refresh_by_tab(column_id, org_id)

    # Jobs appear within ~20s on the live API; give them a moment before
    # concluding nothing was scheduled.
    for _ in range(settings.job_discovery_attempts):
        await asyncio.sleep(settings.job_discovery_interval_seconds)
        jobs = await rox.list_priority_jobs()
        new = [j for j in jobs if j.get("run_id") not in before]
        children = [j for j in new if j.get("task_type") == CELL_JOB]
        if children:
            parents = [
                j["run_id"]
                for j in new
                if j.get("task_type") == COLUMN_JOB and j.get("artifact_id") == column_id
            ]
            log.info(
                "refresh enqueued work",
                column_id=column_id,
                cell_jobs=len(children),
                parents=len(parents),
            )
            return [j["run_id"] for j in children if j.get("run_id")], len(new)

    log.warning(
        "refresh triggered no cell generation",
        column_id=column_id,
        hint="expected for API-created columns; reading existing values instead",
    )
    return [], 0


async def wait_for_jobs(rox: RoxClient, run_ids: list[str], timeout: int) -> int:
    """Block until the given jobs leave a running state. Returns completed count."""
    if not run_ids:
        return 0

    settings = get_settings()
    pending = set(run_ids)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    completed = 0

    while pending and loop.time() < deadline:
        await asyncio.sleep(settings.job_poll_interval_seconds)
        jobs = {j["run_id"]: j for j in await rox.list_priority_jobs() if j.get("run_id")}
        for run_id in list(pending):
            job = jobs.get(run_id)
            if job and str(job.get("current_state", "")).upper() in DONE_STATES:
                pending.discard(run_id)
                completed += 1

    if pending:
        log.warning("jobs still running at timeout", outstanding=len(pending))
    return completed


# ----------------------------------------------------------------------
# Cycle
# ----------------------------------------------------------------------


async def run_research_cycle(
    session: AsyncSession,
    rox: RoxClient,
    *,
    trigger: str = "scheduled",
    create_opportunities: bool = True,
    refresh: bool | None = None,
    notify: bool = True,
) -> ResearchRun:
    settings = get_settings()
    do_refresh = settings.research_refresh_enabled if refresh is None else refresh

    run = ResearchRun(trigger=trigger, status=RunStatus.RUNNING.value)
    session.add(run)
    await session.commit()
    log.info("research run started", run_id=run.id, trigger=trigger, refresh=do_refresh)

    try:
        accounts = await sync_accounts(session, rox)
        run.accounts_scanned = len(accounts)

        columns = await resolve_columns(session, rox)
        if not columns:
            raise RuntimeError("no readable research columns — check the registry")

        org_id = settings.rox_org_id or await rox.discover_org_id()
        if do_refresh and not org_id:
            log.warning("no rox_org_id; skipping refresh and reading current values")
            do_refresh = False
        org_id = normalize_uuid(org_id) if org_id else None

        # --- trigger regeneration for every column, then wait once ---------
        pending_jobs: list[str] = []
        if do_refresh:
            for column, _ref in columns:
                try:
                    children, enqueued = await trigger_refresh(
                        rox, column.rox_column_id, org_id
                    )
                    pending_jobs.extend(children)
                    run.jobs_enqueued += enqueued
                    if children:
                        run.columns_refreshed += 1
                    else:
                        run.cells_timed_out += 1
                except Exception as exc:  # noqa: BLE001 - one column must not kill the run
                    log.error("refresh failed", column=column.key, error=str(exc)[:300])

            run.jobs_completed = await wait_for_jobs(
                rox, pending_jobs, settings.job_poll_timeout_seconds
            )
            await session.commit()

        # --- per-account read ------------------------------------------------
        semaphore = asyncio.Semaphore(settings.research_concurrency)

        async def fetch_cell(
            column: ResearchColumn, account: Account
        ) -> tuple[Account, dict[str, Any] | None]:
            async with semaphore:
                try:
                    data = await rox.get_clever_column_cell(
                        column.rox_column_id, account.rox_entity_id
                    )
                except Exception as exc:  # noqa: BLE001 - one account must not kill the column
                    log.warning(
                        "cell fetch failed",
                        column=column.key,
                        account=account.name,
                        error=str(exc)[:200],
                    )
                    return account, None
                return account, data if isinstance(data, dict) else None

        for column, _ref in columns:
            results = await asyncio.gather(
                *(fetch_cell(column, account) for account in accounts)
            )
            column.last_refreshed_at = utcnow()

            for account, data in results:
                if data is None:
                    continue

                value = data.get("cell_value") or None
                if value:
                    run.cells_fetched += 1

                session.add(
                    ResearchArtifact(
                        run_id=run.id,
                        account_id=account.id,
                        column_id=column.id,
                        cell_value=value,
                        raw=data,
                    )
                )
                run.artifacts_created += 1

        await session.commit()

        if create_opportunities:
            from app.services.opportunities import create_opportunities_for_run

            created = await create_opportunities_for_run(session, run, notify=notify)
            run.opportunities_created = len(created)

        run.status = (
            RunStatus.PARTIAL.value
            if run.cells_timed_out or not run.cells_fetched
            else RunStatus.SUCCEEDED.value
        )

    except Exception as exc:  # noqa: BLE001 - the run record must always close out
        run.status = RunStatus.FAILED.value
        run.error = str(exc)[:2000]
        log.error("research run failed", run_id=run.id, error=str(exc))

    run.finished_at = utcnow()
    await session.commit()
    log.info(
        "research run finished",
        run_id=run.id,
        status=run.status,
        accounts=run.accounts_scanned,
        columns_refreshed=run.columns_refreshed,
        jobs=f"{run.jobs_completed}/{run.jobs_enqueued}",
        cells_fetched=run.cells_fetched,
        opportunities=run.opportunities_created,
    )
    return run
