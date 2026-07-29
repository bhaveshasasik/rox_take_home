"""Aggregate metrics backing the pipeline dashboard.

Every function here is read-only and must degrade gracefully on an empty
database (dashboards load before the first research run finishes) — no
function may raise on zero rows, only return zeroed-out counts.

Time windows
------------
Every metric accepts an optional `start`/`end` pair, half-open as `[start,
end)` so adjacent windows tile without double-counting a boundary row. Each
metric filters on the timestamp of the event it actually measures:

* opportunity cohort (`Opportunity.created_at`) — headline, funnel,
  score calibration, signal performance, account coverage, prospecting yield.
  These answer "of the opportunities *created* in this window, what happened
  to them", so downstream rows (sequences, contacts, emails) are reached by
  joining back to the opportunity rather than filtering on their own
  timestamps. A sequence created today for an opportunity created last month
  belongs to last month's cohort.
* decision time (`Decision.decided_at`) — decision latency, rejection reasons.
* run time (`ResearchRun.started_at`) — run health.

`job_telemetry` is the exception: it reads the live Rox task queue rather than
our database, and keeps its own `lookback_hours` knob.

Timestamps go through `UtcDateTime`, which stores naive UTC and reads back
tz-aware, so bounds arriving from query strings need no conversion here
whether or not they carry an offset.
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
    Opportunity,
    OpportunityStatus,
    OutreachEmail,
    ResearchRun,
    RunStatus,
    Sequence,
    SequenceEnrollment,
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
    Stage.REVIEWED,
    Stage.ACCEPTED,
    Stage.PROSPECTED,
    Stage.SEQUENCED,
    Stage.OUTREACH_SENT,
]

#: Default buckets for score calibration. Deliberately uneven — these are the
#: business-meaningful bands, not equal-width slices. Pass `buckets=N` for
#: equal-width bucketing (deciles being the usual ask).
_SCORE_BANDS = [(0, 59, "0-59"), (60, 74, "60-74"), (75, 89, "75-89"), (90, 100, "90-100")]

_GROUP_BY_UNITS = ("day", "week")


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _window(column, start: datetime | None, end: datetime | None) -> list:
    """Half-open `[start, end)` clauses for `column`; empty when unbounded.

    Bounds may arrive tz-aware (`...Z`, `+05:30`) or naive; `UtcDateTime`
    normalises both when binding, so neither needs converting here.
    """
    clauses = []
    if start is not None:
        clauses.append(column >= start)
    if end is not None:
        clauses.append(column < end)
    return clauses


def _score_bands(buckets: int | None) -> list[tuple[int, int, str]]:
    """Inclusive `(lo, hi, label)` score buckets.

    `buckets=None` keeps the named business bands. `buckets=10` yields deciles
    0-9 … 90-100; the top bucket absorbs the 100 endpoint so a perfect score is
    never dropped.
    """
    if buckets is None:
        return _SCORE_BANDS
    bands = []
    for index in range(buckets):
        lo = round(index * 100 / buckets)
        hi = 100 if index == buckets - 1 else round((index + 1) * 100 / buckets) - 1
        bands.append((lo, hi, f"{lo}-{hi}"))
    return bands


async def _count(session: AsyncSession, *where) -> int:
    query = select(func.count()).select_from(Opportunity)
    for clause in where:
        query = query.where(clause)
    return (await session.execute(query)).scalar_one()


async def overview(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
    buckets: int | None = None,
) -> dict:
    """Everything the dashboard needs in one round trip.

    Deliberately does not expand the rejection-reason time series — that is a
    per-chart request, and inlining it here would grow the payload every
    dashboard load pays for. Call `/reporting/rejection-reasons?group_by=` for it.
    """
    return {
        "headline": await _headline(session, start, end),
        "funnel": await funnel(session, start, end),
        "score_calibration": await score_calibration(session, start, end, buckets),
        "signal_performance": await signal_performance(session, start, end),
        "decision_latency": await decision_latency(session, start, end),
        "rejection_reasons": await rejection_reasons(session, start, end),
        "account_coverage": await account_coverage(session, start, end),
        "prospecting_yield": await prospecting_yield(session, start, end),
        "run_health": await run_health(session, start=start, end=end),
    }


async def _headline(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    window = _window(Opportunity.created_at, start, end)
    total = await _count(session, *window)
    accepted = await _count(
        session, Opportunity.status == OpportunityStatus.ACCEPTED.value, *window
    )
    rejected = await _count(
        session, Opportunity.status == OpportunityStatus.REJECTED.value, *window
    )
    pending = total - accepted - rejected
    decided = accepted + rejected
    return {
        "total_opportunities": total,
        "accepted": accepted,
        "rejected": rejected,
        "pending_review": pending,
        "acceptance_rate": _pct(accepted, decided),
    }


async def funnel(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """How many opportunities reached each stage of the acceptance path.

    Built from durable, never-overwritten signals (status, notified_at,
    presence of a Sequence) rather than the current `stage` column, which a
    rejection overwrites — a rejected opportunity that was notified first
    would otherwise vanish from the `notified` count.

    Sequence-derived steps join back to the opportunity so the whole funnel
    describes one cohort: windowing on `Sequence.created_at` instead would let
    a step report more rows than the step above it.
    """
    window = _window(Opportunity.created_at, start, end)
    total = await _count(session, *window)
    notified = await _count(session, Opportunity.notified_at.is_not(None), *window)
    accepted = await _count(
        session, Opportunity.status == OpportunityStatus.ACCEPTED.value, *window
    )
    rejected = await _count(
        session, Opportunity.status == OpportunityStatus.REJECTED.value, *window
    )

    def _sequence_count(*where):
        query = select(func.count(func.distinct(Sequence.opportunity_id))).join(
            Opportunity, Sequence.opportunity_id == Opportunity.id
        )
        for clause in (*where, *window):
            query = query.where(clause)
        return query

    # Opportunities a human actually decided. `notified` only means the
    # reviewer was emailed — on live data 15 notified vs 5 decided, so using
    # notified as the denominator overstates review throughput threefold.
    reviewed = await _count(
        session, Opportunity.status != OpportunityStatus.NEW.value, *window
    )

    # Contacts found, not sequences created: `run_prospecting` writes a FAILED
    # sequence even when Rox returns nobody, so counting sequences would credit
    # accounts where prospecting found no one.
    prospected_query = select(func.count(func.distinct(Contact.opportunity_id))).join(
        Opportunity, Contact.opportunity_id == Opportunity.id
    )
    for clause in window:
        prospected_query = prospected_query.where(clause)
    prospected = (await session.execute(prospected_query)).scalar_one()

    sequenced = (
        await session.execute(
            _sequence_count(Sequence.status == SequenceStatus.ACTIVE.value)
        )
    ).scalar_one()

    counts = {
        Stage.OPPORTUNITY_CREATED: total,
        Stage.NOTIFIED: notified,
        Stage.REVIEWED: reviewed,
        Stage.ACCEPTED: accepted,
        Stage.PROSPECTED: prospected,
        Stage.SEQUENCED: sequenced,
        Stage.OUTREACH_SENT: 0,
    }
    steps = []
    previous: int | None = None
    for stage in _FUNNEL_STAGES:
        count = counts[stage]
        if previous is None:
            # the first step converts from nothing, so it anchors at 100%
            pct_of_previous = 100.0
        elif previous == 0:
            # undefined rather than 0% — see FunnelStepOut.pct_of_previous
            pct_of_previous = None
        else:
            pct_of_previous = _pct(count, previous)
        steps.append(
            {
                "stage": stage.value,
                "count": count,
                "pct_of_total": _pct(count, total),
                "pct_of_previous": pct_of_previous,
            }
        )
        previous = count
    return {"steps": steps, "rejected": rejected}


async def score_calibration(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
    buckets: int | None = None,
) -> dict:
    """Acceptance rate by score band — is the qualification score predictive?"""
    query = select(Opportunity.qualification_score, Opportunity.status).where(
        Opportunity.status != OpportunityStatus.NEW.value
    )
    for clause in _window(Opportunity.created_at, start, end):
        query = query.where(clause)
    rows = (await session.execute(query)).all()

    bands = []
    total_decided = 0
    for lo, hi, label in _score_bands(buckets):
        decided = [s for score, s in rows if lo <= score <= hi]
        accepted = sum(1 for s in decided if s == OpportunityStatus.ACCEPTED.value)
        total_decided += len(decided)
        bands.append(
            {
                "band": label,
                "lo": lo,
                "hi": hi,
                "decided": len(decided),
                "accepted": accepted,
                "acceptance_rate": _pct(accepted, len(decided)),
            }
        )
    return {"total_decided": total_decided, "buckets": buckets, "bands": bands}


async def signal_performance(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    query = select(Opportunity.signal_type, Opportunity.status)
    for clause in _window(Opportunity.created_at, start, end):
        query = query.where(clause)
    rows = (await session.execute(query)).all()

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


async def decision_latency(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Time-to-decision, with a fallback so the metric survives missing notifications.

    `Decision.latency_seconds` is only recorded when the opportunity was
    notified first; anything decided straight out of the list view stores null.
    Reporting only on those made the median vanish entirely whenever
    notifications were misconfigured, which reads as "no decisions" rather than
    "not measurable". So we fall back to created_at -> decided_at and report
    how many rows came from each basis, letting the UI caveat the number.
    """
    query = select(
        Decision.latency_seconds, Decision.decided_at, Opportunity.created_at
    ).join(Opportunity, Decision.opportunity_id == Opportunity.id)
    for clause in _window(Decision.decided_at, start, end):
        query = query.where(clause)
    rows = (await session.execute(query)).all()

    hours: list[float] = []
    from_notification = 0
    from_creation = 0
    for latency_seconds, decided_at, created_at in rows:
        if latency_seconds is not None:
            hours.append(latency_seconds / 3600)
            from_notification += 1
        elif decided_at is not None and created_at is not None:
            seconds = (decided_at - created_at).total_seconds()
            # backfilled or clock-skewed rows would drag the median negative
            if seconds >= 0:
                hours.append(seconds / 3600)
                from_creation += 1

    return {
        "count": len(rows),
        "measured": len(hours),
        "from_notification": from_notification,
        "from_creation": from_creation,
        "median_hours": round(statistics.median(hours), 2) if hours else None,
        "p90_hours": round(_percentile(hours, 90), 2) if hours else None,
    }


def _period_key(value: datetime, group_by: str) -> str:
    # %G/%V are the ISO year and week, which stay consistent across the
    # year boundary where %Y/%W disagree.
    return value.strftime("%Y-%m-%d" if group_by == "day" else "%G-W%V")


async def rejection_reasons(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
    group_by: str | None = None,
) -> dict:
    """Why opportunities get rejected, optionally bucketed over time.

    The series is emitted long-form — one row per (period, reason_code) — so
    every field keeps a real type. A wide `{period: {reason: count}}` shape
    would generate as an untyped record on the frontend.
    """
    if group_by is not None and group_by not in _GROUP_BY_UNITS:
        raise ValueError(f"group_by must be one of {_GROUP_BY_UNITS}")

    query = select(Decision.reason_code, Decision.decided_at).where(
        Decision.decision == DecisionType.REJECT.value
    )
    for clause in _window(Decision.decided_at, start, end):
        query = query.where(clause)
    rows = (await session.execute(query)).all()

    total = len(rows)
    # null stays null rather than becoming a synthetic "unspecified" code —
    # the field is typed as a nullable ReasonCode, and a made-up member would
    # not validate against it.
    counts = Counter(code for code, _ in rows)
    reasons = [
        {"reason_code": code, "count": count, "pct": _pct(count, total)}
        for code, count in counts.most_common()
    ]

    series: list[dict] = []
    if group_by is not None:
        buckets: Counter = Counter()
        for code, decided_at in rows:
            if decided_at is None:
                continue
            buckets[(_period_key(decided_at, group_by), code)] += 1
        series = [
            {"period": period, "reason_code": code, "count": count}
            # sort by period, then reason for a stable, chartable ordering.
            # `or ""` keeps the null reason sortable alongside real codes.
            for (period, code), count in sorted(
                buckets.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ]

    return {
        "total_rejections": total,
        "reasons": reasons,
        "group_by": group_by,
        "series": series,
    }


async def account_coverage(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Which accounts produced an opportunity — within the window, if given.

    `total_accounts` stays unwindowed on purpose: the denominator is "accounts
    we know about", not "accounts created during the window", or coverage would
    read 100% for any window in which no new accounts synced.
    """
    total_accounts = (
        await session.execute(select(func.count()).select_from(Account))
    ).scalar_one()
    covered_query = select(func.distinct(Opportunity.account_id))
    for clause in _window(Opportunity.created_at, start, end):
        covered_query = covered_query.where(clause)
    covered_ids = set((await session.execute(covered_query)).scalars().all())
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


async def prospecting_yield(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Downstream output per accepted opportunity, for one opportunity cohort.

    Contacts and emails are reached by joining back to the opportunity so every
    ratio here shares the `accepted` denominator.
    """
    window = _window(Opportunity.created_at, start, end)
    accepted = await _count(
        session, Opportunity.status == OpportunityStatus.ACCEPTED.value, *window
    )

    def _scoped(query):
        for clause in window:
            query = query.where(clause)
        return query

    sequences_active = (
        await session.execute(
            _scoped(
                select(func.count())
                .select_from(Sequence)
                .join(Opportunity, Sequence.opportunity_id == Opportunity.id)
                .where(Sequence.status == SequenceStatus.ACTIVE.value)
            )
        )
    ).scalar_one()
    total_contacts = (
        await session.execute(
            _scoped(
                select(func.count())
                .select_from(Contact)
                .join(Opportunity, Contact.opportunity_id == Opportunity.id)
            )
        )
    ).scalar_one()
    total_emails = (
        await session.execute(
            _scoped(
                select(func.count())
                .select_from(OutreachEmail)
                .join(
                    SequenceEnrollment,
                    OutreachEmail.enrollment_id == SequenceEnrollment.id,
                )
                .join(Sequence, SequenceEnrollment.sequence_id == Sequence.id)
                .join(Opportunity, Sequence.opportunity_id == Opportunity.id)
            )
        )
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


async def run_health(
    session: AsyncSession,
    limit: int = 20,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    query = select(ResearchRun).order_by(ResearchRun.started_at.desc()).limit(limit)
    for clause in _window(ResearchRun.started_at, start, end):
        query = query.where(clause)
    runs = (await session.execute(query)).scalars().all()
    succeeded = sum(1 for r in runs if r.status == RunStatus.SUCCEEDED.value)
    return {
        "runs_considered": len(runs),
        "success_rate": _pct(succeeded, len(runs)),
        "total_cells_fetched": sum(r.cells_fetched for r in runs),
        # a rising share here means research is arriving in a shape the parser
        # no longer understands
        "total_cells_unscoreable": sum(r.cells_unscoreable for r in runs),
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
                "cells_unscoreable": r.cells_unscoreable,
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

    # busiest task type first, and within it the most common state first, so
    # the table reads top-down without the client having to sort a record
    by_task_type = sorted(
        (
            {
                "task_type": task_type,
                "total": sum(states.values()),
                "states": [
                    {"state": state, "count": count}
                    for state, count in states.most_common()
                ],
            }
            for task_type, states in by_type.items()
        ),
        key=lambda row: row["total"],
        reverse=True,
    )

    return {
        "lookback_hours": lookback_hours,
        "total_jobs": len(jobs),
        "by_task_type": by_task_type,
        "avg_cell_generation_seconds": (
            round(sum(durations) / len(durations), 1) if durations else None
        ),
    }
