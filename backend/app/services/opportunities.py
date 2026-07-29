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
from app.services.notifications import send_digest
#: Legacy path. Kept as the fallback for artifacts that have not been
#: extracted — 357 of 432 at the time of writing — and used for nothing else.
from app.services.parsing import ParsedCell, parse_cell
from app.services.rationale import expand_rationale
from app.signals.elaboration import write_brief
from app.signals.service import scored_for_artifact

log = get_logger(__name__)


#: How much a new score must clear an open opportunity's score by to
#: supersede it. The score's quantum is 10 — the base is driver confidence
#: × 10 — and secondary factors jitter by ±5; observed run-over-run drift on
#: the same account (fresh research, same fundamentals) was −5, −11 and −26,
#: so a margin of 10 sits inside noise and would churn. 15 is one full
#: confidence step plus jitter. The deadlock cases this exists for — an
#: account stuck behind a 0-score opportunity while an 85 waits — clear any
#: sane margin.
SUPERSEDE_MARGIN = 15


async def should_skip_account(
    session: AsyncSession,
    account_id: str,
    signal_type: str | None = None,
    *,
    new_score: int | None,
    ignore_cooldown: bool = False,
) -> str | None:
    """Return a reason to skip this account, or None to proceed.

    Deduped per **account**, not per signal type. Research now comes from one
    primary Rox column, so `signal_type` is derived from the rationale text and
    can drift between runs for the same underlying signal — keying on it would
    let near-duplicate opportunities through.

    Two guards:
      1. An undecided opportunity is already waiting on the reviewer — don't
         stack duplicates on their queue. Score-aware when `new_score` is
         given: a candidate clearing the open score by `SUPERSEDE_MARGIN`
         closes the open one as superseded and proceeds, because otherwise an
         unreviewed low score blocks its account forever — the cooldown never
         engages, since it keys off a decision that never happens.
      2. A decision was made recently — respect it for a cooldown window rather
         than re-asking every cycle.
    """
    settings = get_settings()

    # Required, not defaulted: a caller that forgets the score silently
    # falls back to unconditional blocking, which regenerates the exact
    # deadlock the score-aware guard exists to fix. Passing None is allowed
    # but must be a visible, deliberate choice.
    if new_score is None:
        log.warning(
            "dedupe without a score — open opportunities block unconditionally",
            account_id=account_id,
        )

    open_existing = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.account_id == account_id,
                Opportunity.status == OpportunityStatus.NEW.value,
            )
        )
    ).scalars().first()
    if open_existing is not None:
        supersedes = (
            new_score is not None
            and new_score >= open_existing.qualification_score + SUPERSEDE_MARGIN
        )
        if not supersedes:
            return f"open opportunity {open_existing.id} already pending review"

        # Terminal but undecided: no Decision row, decided_at stays NULL, so
        # neither the cooldown nor any decision-based report picks it up.
        open_existing.status = OpportunityStatus.SUPERSEDED.value
        log.info(
            "opportunity superseded",
            account_id=account_id,
            superseded_id=open_existing.id,
            old_score=open_existing.qualification_score,
            new_score=new_score,
        )

    if ignore_cooldown:
        # Per-run override from the manual trigger. Only the cooldown: the
        # open-opportunity guard above still applies, because superseding is
        # the sanctioned way past it and stacking duplicates is not.
        return None

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
    session: AsyncSession,
    run: ResearchRun,
    *,
    notify: bool = True,
    ignore_cooldown: bool = False,
) -> list[Opportunity]:
    """Parse each account's research artifact from this run and create opportunities.

    Notification happens once, after every opportunity in this run is
    created: one digest per producing run, containing everything currently
    eligible — never one email per account a single run happens to qualify.
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

        # Structured signals when the artifact has been extracted, the legacy
        # parser when it has not. Both produce the same three values, so the
        # rest of this loop does not care which one ran.
        extracted = await scored_for_artifact(session, artifact.id)
        if extracted is not None:
            scored, signals = extracted
            parsed = ParsedCell(
                score=scored.score,
                signal_type=(
                    scored.signal_type.value if scored.signal_type else "other"
                ),
                rationale="\n\n".join(
                    s.rationale for s in signals if s.rationale and not s.is_absence
                )
                or None,
                needs_review=scored.needs_review,
                raw=artifact.cell_value or "",
            )
        else:
            signals = []
            parsed = parse_cell(artifact.cell_value)

        if parsed.score is None:
            # `info`, not `debug`: this is research we fetched and paid for and
            # then discarded. At debug it stayed invisible while a format change
            # silently dropped every prose cell for days.
            log.info(
                "no scoreable research",
                account=account.name,
                cell_chars=len(artifact.cell_value or ""),
            )
            run.cells_unscoreable += 1
            continue

        if parsed.score < settings.opportunity_score_threshold:
            log.debug(
                "below threshold",
                account=account.name,
                score=parsed.score,
                threshold=settings.opportunity_score_threshold,
            )
            continue

        skip_reason = await should_skip_account(
            session,
            artifact.account_id,
            new_score=parsed.score,
            ignore_cooldown=ignore_cooldown,
        )
        if skip_reason:
            log.info(
                "skipping duplicate opportunity", account=account.name, reason=skip_reason
            )
            continue

        headline = f"Qualified signal at {account.name}"
        rationale = parsed.rationale or headline

        # Strong signals earn the expensive LLM pass. Borderline opportunities
        # keep the cheap version — no call, no latency.
        if parsed.score >= settings.llm_expansion_score_threshold:
            if signals:
                # Elaborate the structured signals rather than re-reading the
                # raw narrative. Every claim then traces to a typed signal, a
                # verbatim evidence span and a whitelisted source, and the
                # extraction pass is not paid for twice over the same prose.
                brief = await write_brief(account.name, signals)
                if brief is not None:
                    headline = brief.headline
                    rationale = f"{brief.rationale}\n\n{brief.why_now}".strip()
            else:
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
        # One digest per producing run. Eligibility is the shared query, so
        # the digest also sweeps qualifying backlog from earlier runs that
        # never made it into one — and sends nothing when nothing qualifies.
        await send_digest(session, trigger=run.trigger, run_id=run.id)

    return created
