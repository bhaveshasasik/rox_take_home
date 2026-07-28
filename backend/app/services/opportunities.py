"""Requirement 2: turn research into scored opportunities.

Research is recurring, so the same signal will resurface on every run. Dedupe
is therefore load-bearing, not a nicety: without it the reviewer gets the same
opportunity every 15 minutes and stops trusting the notifications.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger
from app.models import (
    Account,
    Opportunity,
    OpportunityResearchLink,
    OpportunityStatus,
    ResearchArtifact,
    ResearchRun,
    Stage,
    utcnow,
)
from app.services.notifications import notify_batch
from app.services.parsing import parse_cell
from app.services.rationale import expand_rationale

log = get_logger(__name__)


async def should_skip_account(
    session: AsyncSession, account_id: str, signal_type: str | None = None
) -> str | None:
    """Return a reason to skip this account, or None to proceed.

    Deduped per **account**, not per signal type. Research now comes from one
    primary Rox column, so `signal_type` is derived from the rationale text and
    can drift between runs for the same underlying signal — keying on it would
    let near-duplicate opportunities through.

    Two guards:
      1. An undecided opportunity is already waiting on the reviewer — don't
         stack duplicates on their queue.
      2. A decision was made recently — respect it for a cooldown window rather
         than re-asking every cycle.
    """
    settings = get_settings()

    open_existing = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.account_id == account_id,
                Opportunity.status == OpportunityStatus.NEW.value,
            )
        )
    ).scalars().first()
    if open_existing is not None:
        return f"open opportunity {open_existing.id} already pending review"

    cutoff = utcnow() - timedelta(days=settings.opportunity_cooldown_days)
    recent = (
        await session.execute(
            select(Opportunity)
            .where(
                Opportunity.account_id == account_id,
                Opportunity.status != OpportunityStatus.NEW.value,
                Opportunity.decided_at.is_not(None),
                Opportunity.decided_at >= cutoff.replace(tzinfo=None),
            )
            .order_by(Opportunity.decided_at.desc())
        )
    ).scalars().first()
    if recent is not None:
        return (
            f"decided {recent.status} within the "
            f"{settings.opportunity_cooldown_days}d cooldown"
        )

    return None


async def create_opportunities_for_run(
    session: AsyncSession, run: ResearchRun, *, notify: bool = True
) -> list[Opportunity]:
    """Parse each account's research artifact from this run and create opportunities.

    Notification happens once, after every opportunity in this run is
    created, rather than per opportunity — the queue batches into one email
    per `notification_batch_size` instead of flooding the reviewer's inbox
    with one email per account a single run happens to qualify.
    """
    settings = get_settings()

    artifacts = (
        (await session.execute(select(ResearchArtifact).where(ResearchArtifact.run_id == run.id)))
        .scalars()
        .all()
    )
    if not artifacts:
        return []

    accounts = {
        a.id: a for a in (await session.execute(select(Account))).scalars().all()
    }

    created: list[Opportunity] = []

    for artifact in artifacts:
        account = accounts.get(artifact.account_id)
        if account is None:
            continue

        parsed = parse_cell(artifact.cell_value)
        if parsed.score is None:
            log.debug("no scoreable research", account=account.name)
            continue

        if parsed.score < settings.opportunity_score_threshold:
            log.debug(
                "below threshold",
                account=account.name,
                score=parsed.score,
                threshold=settings.opportunity_score_threshold,
            )
            continue

        skip_reason = await should_skip_account(session, artifact.account_id)
        if skip_reason:
            log.info(
                "skipping duplicate opportunity", account=account.name, reason=skip_reason
            )
            continue

        headline = f"Qualified signal at {account.name}"
        rationale = parsed.rationale or headline

        # Strong signals earn the expensive LLM pass: the terse evidence-bullet
        # rationale gets replaced by a version written from Rox's full research
        # narrative (artifact.raw.output.text), not just the truncated cell_value.
        # Borderline opportunities keep the cheap version — no call, no latency.
        if parsed.score >= settings.llm_expansion_score_threshold:
            narrative = ((artifact.raw or {}).get("output") or {}).get("text") or ""
            expanded = await expand_rationale(account.name, narrative)
            if expanded is not None:
                headline = expanded.headline
                rationale = expanded.rationale

        opportunity = Opportunity(
            account_id=artifact.account_id,
            run_id=run.id,
            title=f"{account.name}: {headline}"[:255],
            rationale=rationale,
            qualification_score=parsed.score,
            signal_type=parsed.signal_type or "general_signal",
            needs_review=parsed.needs_review,
            status=OpportunityStatus.NEW.value,
            stage=Stage.OPPORTUNITY_CREATED.value,
        )
        session.add(opportunity)
        await session.flush()

        session.add(
            OpportunityResearchLink(opportunity_id=opportunity.id, artifact_id=artifact.id)
        )

        created.append(opportunity)
        log.info(
            "opportunity created",
            account=account.name,
            score=parsed.score,
            needs_review=parsed.needs_review,
        )

    await session.commit()

    if notify:
        await notify_batch(session)

    return created
