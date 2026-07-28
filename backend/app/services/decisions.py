"""Requirement 4: capture the human accept/reject decision.

`latency_seconds` is recorded here rather than derived at read time, because
the time-to-decision report needs it and notified_at can be backfilled by a
notification retry after the fact.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import (
    Decision,
    DecisionType,
    Opportunity,
    OpportunityStatus,
    Stage,
    utcnow,
)

log = get_logger(__name__)


class AlreadyDecidedError(RuntimeError):
    def __init__(self, opportunity: Opportunity) -> None:
        super().__init__(
            f"opportunity {opportunity.id} was already {opportunity.status}"
        )
        self.opportunity = opportunity


async def record_decision(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    decision: DecisionType,
    decided_by: str | None = None,
    reason_code: str | None = None,
    notes: str | None = None,
) -> Decision:
    """Record accept/reject. Idempotency is enforced: re-deciding raises."""
    if opportunity.status != OpportunityStatus.NEW.value:
        raise AlreadyDecidedError(opportunity)

    now = utcnow()
    latency = None
    if opportunity.notified_at is not None:
        notified = opportunity.notified_at
        if notified.tzinfo is None:
            notified = notified.replace(tzinfo=now.tzinfo)
        latency = (now - notified).total_seconds()

    record = Decision(
        opportunity_id=opportunity.id,
        decision=decision.value,
        decided_by=decided_by,
        reason_code=reason_code,
        notes=notes,
        decided_at=now,
        latency_seconds=latency,
    )
    session.add(record)

    opportunity.decided_at = now
    if decision is DecisionType.ACCEPT:
        opportunity.status = OpportunityStatus.ACCEPTED.value
        opportunity.stage = Stage.ACCEPTED.value
    else:
        opportunity.status = OpportunityStatus.REJECTED.value
        opportunity.stage = Stage.REJECTED.value

    await session.commit()
    log.info(
        "decision recorded",
        opportunity_id=opportunity.id,
        decision=decision.value,
        reason_code=reason_code,
        latency_seconds=latency,
    )
    return record
