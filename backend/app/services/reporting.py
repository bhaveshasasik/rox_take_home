"""Aggregate metrics backing the pipeline dashboard.

Every function here is read-only and must degrade gracefully on an empty
database (dashboards load before the first research run finishes) — no
function may raise on zero rows, only return zeroed-out counts.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Contact,
    Decision,
    DecisionType,
    Notification,
    Opportunity,
    OpportunityStatus,
    OutreachEmail,
    ResearchRun,
    RunStatus,
    Sequence,
    SequenceStatus,
    Stage,
)
from app.rox.client import RoxClient
from app.services.notifications import signal_label

#: Order the acceptance-path funnel is shown in. `rejected` is reported
#: separately, since a rejection leaves this path rather than advancing along it.
_FUNNEL_STAGES = [
    Stage.OPPORTUNITY_CREATED,
    Stage.NOTIFIED,
    Stage.ACCEPTED,
    Stage.PROSPECTED,
    Stage.SEQUENCED,
    Stage.OUTREACH_SENT,
]

_SCORE_BANDS = [(0, 59, "0-59"), (60, 74, "60-74"), (75, 89, "75-89"), (90, 100, "90-100")]


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


async def _count(session: AsyncSession, *where) -> int:
    query = select(func.count()).select_from(Opportunity)
    for clause in where:
        query = query.where(clause)
    return (await session.execute(query)).scalar_one()


async def overview(session: AsyncSession) -> dict:
    """Everything the dashboard needs in one round trip."""
    return {
        "headline": await _headline(session),
        "funnel": await funnel(session),
        "score_calibration": await score_calibration(session),
        "signal_performance": await signal_performance(session),
        "decision_latency": await decision_latency(session),
        "rejection_reasons": await rejection_reasons(session),
        "account_coverage": await account_coverage(session),
        "prospecting_yield": await prospecting_yield(session),
        "run_health": await run_health(session),
    }


async def _headline(session: AsyncSession) -> dict:
    total = await _count(session)
    accepted = await _count(session, Opportunity.status == OpportunityStatus.ACCEPTED.value)
    rejected = await _count(session, Opportunity.status == OpportunityStatus.REJECTED.value)
    pending = total - accepted - rejected
    decided = accepted + rejected
    return {
        "total_opportunities": total,
        "accepted": accepted,
        "rejected": rejected,
        "pending_review": pending,
        "acceptance_rate": _pct(accepted, decided),
    }


async def funnel(session: AsyncSession) -> dict:
    """How many opportunities reached each stage of the acceptance path.

    Built from durable, never-overwritten signals (status, notified_at,
    presence of a Sequence) rather than the current `stage` column, which a
    rejection overwrites — a rejected opportunity that was notified first
    would otherwise vanish from the `notified` count.
    """
    total = await _count(session)
    notified = await _count(session, Opportunity.notified_at.is_not(None))
    accepted = await _count(session, Opportunity.status == OpportunityStatus.ACCEPTED.value)
    rejected = await _count(session, Opportunity.status == OpportunityStatus.REJECTED.value)

    prospected = (
        await session.execute(
            select(func.count(func.distinct(Sequence.opportunity_id)))
        )
    ).scalar_one()
    sequenced = (
        await session.execute(
            select(func.count(func.distinct(Sequence.opportunity_id))).where(
                Sequence.status == SequenceStatus.ACTIVE.value
            )
        )
    ).scalar_one()

    counts = {
        Stage.OPPORTUNITY_CREATED: total,
        Stage.NOTIFIED: notified,
        Stage.ACCEPTED: accepted,
        Stage.PROSPECTED: prospected,
        Stage.SEQUENCED: sequenced,
        Stage.OUTREACH_SENT: 0,
    }
    steps = [
        {
            "stage": stage.value,
            "count": counts[stage],
            "pct_of_total": _pct(counts[stage], total),
        }
        for stage in _FUNNEL_STAGES
    ]
    return {"steps": steps, "rejected": rejected}


async def score_calibration(session: AsyncSession) -> dict:
    """Acceptance rate by score band — is the qualification score predictive?"""
    rows = (
        (
            await session.execute(
                select(Opportunity.qualification_score, Opportunity.status).where(
                    Opportunity.status != OpportunityStatus.NEW.value
                )
            )
        )
        .all()
    )

    bands = []
    total_decided = 0
    for lo, hi, label in _SCORE_BANDS:
        decided = [s for score, s in rows if lo <= score <= hi]
        accepted = sum(1 for s in decided if s == OpportunityStatus.ACCEPTED.value)
        total_decided += len(decided)
        bands.append(
            {
                "band": label,
                "decided": len(decided),
                "accepted": accepted,
                "acceptance_rate": _pct(accepted, len(decided)),
            }
        )
    return {"total_decided": total_decided, "bands": bands}


async def signal_performance(session: AsyncSession) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(Opportunity.signal_type, Opportunity.status)
            )
        )
        .all()
    )

    by_signal: dict[str, Counter] = defaultdict(Counter)
    for signal_type, status in rows:
        by_signal[signal_type]["created"] += 1
        if status == OpportunityStatus.ACCEPTED.value:
            by_signal[signal_type]["accepted"] += 1
        elif status == OpportunityStatus.REJECTED.value:
            by_signal[signal_type]["rejected"] += 1

    results = []
    for signal_type, counts in by_signal.items():
        decided = counts["accepted"] + counts["rejected"]
        results.append(
            {
                "signal_type": signal_type,
                "label": signal_label(signal_type),
                "created": counts["created"],
                "accepted": counts["accepted"],
                "rejected": counts["rejected"],
                "acceptance_rate": _pct(counts["accepted"], decided),
            }
        )
    results.sort(key=lambda r: r["created"], reverse=True)
    return results


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


async def decision_latency(session: AsyncSession) -> dict:
    rows = (
        (await session.execute(select(Decision.latency_seconds))).scalars().all()
    )
    hours = [s / 3600 for s in rows if s is not None]
    return {
        "count": len(rows),
        "median_hours": round(statistics.median(hours), 2) if hours else None,
        "p90_hours": round(_percentile(hours, 90), 2) if hours else None,
    }


async def rejection_reasons(session: AsyncSession) -> dict:
    rows = (
        (
            await session.execute(
                select(Decision.reason_code).where(
                    Decision.decision == DecisionType.REJECT.value
                )
            )
        )
        .scalars()
        .all()
    )
    total = len(rows)
    counts = Counter(code or "unspecified" for code in rows)
    reasons = [
        {"reason_code": code, "count": count, "pct": _pct(count, total)}
        for code, count in counts.most_common()
    ]
    return {"total_rejections": total, "reasons": reasons}


async def account_coverage(session: AsyncSession) -> dict:
    total_accounts = (
        await session.execute(select(func.count()).select_from(Account))
    ).scalar_one()
    covered_ids = set(
        (
            await session.execute(
                select(func.distinct(Opportunity.account_id))
            )
        )
        .scalars()
        .all()
    )
    accounts = (await session.execute(select(Account))).scalars().all()
    uncovered = [
        {"id": a.id, "name": a.name} for a in accounts if a.id not in covered_ids
    ]
    return {
        "total_accounts": total_accounts,
        "accounts_with_opportunities": len(covered_ids),
        "coverage_rate": _pct(len(covered_ids), total_accounts),
        "uncovered_accounts": uncovered,
    }


async def prospecting_yield(session: AsyncSession) -> dict:
    accepted = await _count(session, Opportunity.status == OpportunityStatus.ACCEPTED.value)
    sequences_active = (
        await session.execute(
            select(func.count()).select_from(Sequence).where(
                Sequence.status == SequenceStatus.ACTIVE.value
            )
        )
    ).scalar_one()
    total_contacts = (
        await session.execute(select(func.count()).select_from(Contact))
    ).scalar_one()
    total_emails = (
        await session.execute(select(func.count()).select_from(OutreachEmail))
    ).scalar_one()

    return {
        "accepted_opportunities": accepted,
        "sequences_active": sequences_active,
        "activation_rate": _pct(sequences_active, accepted),
        "total_contacts": total_contacts,
        "total_emails_drafted": total_emails,
        "avg_contacts_per_accepted": (
            round(total_contacts / accepted, 1) if accepted else 0.0
        ),
    }


async def run_health(session: AsyncSession, limit: int = 20) -> dict:
    runs = (
        (
            await session.execute(
                select(ResearchRun).order_by(ResearchRun.started_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    succeeded = sum(1 for r in runs if r.status == RunStatus.SUCCEEDED.value)
    return {
        "runs_considered": len(runs),
        "success_rate": _pct(succeeded, len(runs)),
        "total_cells_fetched": sum(r.cells_fetched for r in runs),
        "recent_runs": [
            {
                "id": r.id,
                "trigger": r.trigger,
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "duration_seconds": r.duration_seconds,
                "accounts_scanned": r.accounts_scanned,
                "cells_fetched": r.cells_fetched,
                "opportunities_created": r.opportunities_created,
            }
            for r in runs
        ],
    }


async def job_telemetry(rox: RoxClient, lookback_hours: int = 24) -> dict:
    """Live Rox task-queue health — task types, states, cell-generation time."""
    jobs = await rox.list_priority_jobs(num_lookback_hours=lookback_hours)

    by_type: dict[str, Counter] = defaultdict(Counter)
    durations: list[float] = []
    for job in jobs:
        task_type = job.get("task_type") or "unknown"
        by_type[task_type][str(job.get("current_state") or "unknown")] += 1

        if task_type == "CUSTOM_CELL_GENERATION" and job.get("current_state") == "COMPLETED":
            created, modified = job.get("created_on"), job.get("last_modified")
            if created and modified:
                try:
                    start = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    end = datetime.fromisoformat(str(modified).replace("Z", "+00:00"))
                    durations.append((end - start).total_seconds())
                except ValueError:
                    pass

    return {
        "lookback_hours": lookback_hours,
        "total_jobs": len(jobs),
        "by_task_type": {t: dict(states) for t, states in by_type.items()},
        "avg_cell_generation_seconds": (
            round(sum(durations) / len(durations), 1) if durations else None
        ),
    }
