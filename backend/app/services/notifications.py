"""Requirement 3: notify the assigned user when an opportunity is created.

Email is the only channel, so this is just: format a message, send it over
SMTP, and record what happened. Every delivery attempt is persisted as a
Notification row before it is sent, so an SMTP outage is visible in the UI
and retryable rather than silent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.logging_config import get_logger
from app.models import (
    Account,
    Channel,
    Notification,
    NotificationStatus,
    Opportunity,
    OpportunityStatus,
    Stage,
    utcnow,
)
from app.services.rationale import write_notification_summary

log = get_logger(__name__)


def signal_label(signal_type: str) -> str:
    """Human-readable label for a signal_type slug.

    signal_type is derived by slugifying whatever category label Rox's own
    research emits ("Growth / Expansion" -> "growth_expansion") — an open
    vocabulary, not a fixed set — so there's no fixed dict to map from; just
    reverse the slugging.
    """
    return (signal_type or "").replace("_", " ").title()


@dataclass
class OpportunityMessage:
    account_name: str
    title: str
    signal_label: str
    summary: str
    link: str
    needs_review: bool

    @property
    def subject(self) -> str:
        return f"New opportunity: {self.account_name}"

    def as_text(self) -> str:
        review = "\n\n[!] Parsed with low confidence - please sanity-check the research." if self.needs_review else ""
        return (
            f"{self.title}\n\n"
            f"Account:  {self.account_name}\n"
            f"Signal:   {self.signal_label}\n\n"
            f"{self.summary}"
            f"{review}\n\n"
            f"Review and accept/reject:\n{self.link}"
        )


async def resolve_account_name(
    session: AsyncSession, opportunity: Opportunity
) -> str:
    """Fetch the account name without relying on the caller having eager-loaded
    the relationship — lazy loading raises MissingGreenlet under async SQLAlchemy.
    """
    account = (
        await session.execute(
            select(Account.name).where(Account.id == opportunity.account_id)
        )
    ).scalar_one_or_none()
    return account or "Unknown account"


async def build_message(opportunity: Opportunity, account_name: str) -> OpportunityMessage:
    settings = get_settings()
    label = signal_label(opportunity.signal_type)
    rationale = opportunity.rationale or ""
    summary = await write_notification_summary(account_name, label, rationale) or rationale
    return OpportunityMessage(
        account_name=account_name,
        title=opportunity.title,
        signal_label=label,
        summary=summary,
        link=f"{settings.app_base_url.rstrip('/')}/opportunities/{opportunity.id}",
        needs_review=opportunity.needs_review,
    )


# ----------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------

SendFn = Callable[[OpportunityMessage], Awaitable[dict]]


async def _send_email(*, to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_username
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname=s.smtp_host,
        port=s.smtp_port,
        username=s.smtp_username,
        password=s.smtp_password,
        start_tls=s.smtp_port == 587,
        use_tls=s.smtp_port == 465,
        timeout=20,
    )


def _configured_recipient() -> str | None:
    """The configured recipient, or None if SMTP isn't set up."""
    settings = get_settings()
    if settings.email_enabled and settings.notify_email_to:
        return settings.notify_email_to
    return None


async def _default_send(to: str, message: OpportunityMessage) -> dict:
    await _send_email(to=to, subject=message.subject, body=message.as_text())
    return {"to": to, "subject": message.subject, "body": message.as_text()}


async def _attempt(record: Notification, send: SendFn, message: OpportunityMessage) -> None:
    record.attempts += 1
    try:
        record.payload = await send(message)
        record.status = NotificationStatus.SENT.value
        record.sent_at = utcnow()
        record.error = None
        log.info("notification sent", opportunity_id=record.opportunity_id)
    except Exception as exc:  # noqa: BLE001 - failure is recorded, not raised
        record.status = NotificationStatus.FAILED.value
        record.error = str(exc)[:2000]
        log.error(
            "notification failed", opportunity_id=record.opportunity_id, error=str(exc)
        )


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


async def notify_opportunity(
    session: AsyncSession,
    opportunity: Opportunity,
    send: SendFn | None = None,
) -> Notification:
    """Email the assigned user about one newly created opportunity.

    `send` is an injection point for tests to capture the message instead of
    hitting real SMTP; production callers leave it unset.
    """
    to = _configured_recipient()
    record = Notification(
        opportunity_id=opportunity.id,
        channel=Channel.EMAIL.value,
        recipient=to,
        status=NotificationStatus.PENDING.value,
    )
    session.add(record)
    await session.flush()

    if send is None and to is None:
        record.status = NotificationStatus.FAILED.value
        record.error = "no email channel configured"
        log.warning(
            "notification not sent — no email channel configured",
            opportunity_id=opportunity.id,
        )
    else:
        account_name = await resolve_account_name(session, opportunity)
        message = await build_message(opportunity, account_name)
        await _attempt(record, send or (lambda m: _default_send(to, m)), message)

    if record.status == NotificationStatus.SENT.value:
        opportunity.notified_at = utcnow()
        if opportunity.stage == Stage.OPPORTUNITY_CREATED.value:
            opportunity.stage = Stage.NOTIFIED.value

    await session.commit()
    return record


async def retry_notification(
    session: AsyncSession, notification_id: str, send: SendFn | None = None
) -> Notification | None:
    record = (
        await session.execute(
            select(Notification)
            .where(Notification.id == notification_id)
            .options(
                selectinload(Notification.opportunity).selectinload(Opportunity.account)
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return None

    to = _configured_recipient()
    if send is None:
        if to is None:
            record.error = "no email channel configured"
            await session.commit()
            return record
        send = lambda m: _default_send(to, m)

    opportunity = record.opportunity
    message = await build_message(
        opportunity, await resolve_account_name(session, opportunity)
    )
    await _attempt(record, send, message)

    if record.status == NotificationStatus.SENT.value:
        opportunity.notified_at = opportunity.notified_at or utcnow()
        if opportunity.stage == Stage.OPPORTUNITY_CREATED.value:
            opportunity.stage = Stage.NOTIFIED.value

    await session.commit()
    return record


def _entry_lines(opportunity: Opportunity, account_name: str, base_url: str) -> list[str]:
    """The block for one opportunity in a multi-opportunity email.

    Account, rationale, review link — never the score, this is a reviewer's
    inbox, not a leaderboard. Shared by the digest and the batch notification
    so both list opportunities the same way.
    """
    return [
        "",
        account_name,
        (opportunity.rationale or "").strip(),
        f"Review: {base_url}/opportunities/{opportunity.id}",
    ]


# ----------------------------------------------------------------------
# Batch — the top N queued opportunities, sent as one email
# ----------------------------------------------------------------------


async def notify_batch(
    session: AsyncSession, batch_size: int | None = None
) -> dict:
    """Once `batch_size` opportunities are queued for review, email the top
    ones (by score) in a single message instead of one email per opportunity.

    A research run can qualify several accounts at once; notifying per
    opportunity floods the reviewer's inbox with that many emails back to
    back. Batching means the send cadence tracks how fast opportunities
    actually queue up, not the size of any one run — accumulates across runs
    if a single run doesn't reach the threshold on its own.
    """
    settings = get_settings()
    size = batch_size if batch_size is not None else settings.notification_batch_size
    base_url = settings.app_base_url.rstrip("/")

    pending = (
        await session.execute(
            select(Opportunity, Account.name)
            .join(Account, Opportunity.account_id == Account.id)
            .where(
                Opportunity.status == OpportunityStatus.NEW.value,
                Opportunity.notified_at.is_(None),
            )
            .order_by(Opportunity.qualification_score.desc())
        )
    ).all()

    if len(pending) < size:
        return {"sent": False, "queued": len(pending)}

    batch = pending[:size]

    to = _configured_recipient()
    if to is None:
        log.warning("batch not sent — no email channel configured")
        return {"sent": False, "queued": len(pending)}

    records = []
    for opp, _account_name in batch:
        record = Notification(
            opportunity_id=opp.id,
            channel=Channel.EMAIL.value,
            recipient=to,
            status=NotificationStatus.PENDING.value,
        )
        session.add(record)
        records.append(record)
    await session.flush()

    lines = [f"{size} new opportunities ready for review", "-" * 33]
    for opp, account_name in batch:
        lines.extend(_entry_lines(opp, account_name, base_url))
    body = "\n".join(lines).rstrip() + "\n"
    subject = f"[Rox Pipeline] {size} new opportunities to review"

    try:
        await _send_email(to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 - surface the failure, don't raise
        for record in records:
            record.status = NotificationStatus.FAILED.value
            record.error = str(exc)[:2000]
        await session.commit()
        log.error("batch notification failed", error=str(exc)[:2000])
        return {"sent": False, "queued": len(pending), "error": str(exc)[:2000]}

    now = utcnow()
    for record, (opp, _account_name) in zip(records, batch):
        record.status = NotificationStatus.SENT.value
        record.sent_at = now
        opp.notified_at = now
        if opp.stage == Stage.OPPORTUNITY_CREATED.value:
            opp.stage = Stage.NOTIFIED.value

    await session.commit()
    log.info("batch notification sent", count=len(batch), recipient=to)
    return {"sent": True, "count": len(batch), "recipient": to}


# ----------------------------------------------------------------------
# Digest — everything currently awaiting review, in one email
# ----------------------------------------------------------------------


async def build_digest(session: AsyncSession) -> tuple[str, str, int]:
    """Return (subject, body, count) for every opportunity pending review."""
    settings = get_settings()
    base_url = settings.app_base_url.rstrip("/")

    rows = (
        await session.execute(
            select(Opportunity, Account.name)
            .join(Account, Opportunity.account_id == Account.id)
            .where(Opportunity.status == OpportunityStatus.NEW.value)
            .order_by(Opportunity.created_at.desc())
        )
    ).all()

    lines = ["Today's Opportunities", "-" * 21, ""]
    if not rows:
        lines.append("Nothing is waiting for review right now.")
    else:
        lines.append(f"{len(rows)} account{'s' if len(rows) != 1 else ''} ready for your review.")
        for opp, account_name in rows:
            lines.extend(_entry_lines(opp, account_name, base_url))

    body = "\n".join(lines).rstrip() + "\n"
    subject = (
        f"[Rox Pipeline] {len(rows)} opportunit{'ies' if len(rows) != 1 else 'y'} to review today"
        if rows
        else "[Rox Pipeline] No opportunities to review today"
    )
    return subject, body, len(rows)


async def send_digest(session: AsyncSession) -> dict:
    """Email everything currently awaiting review as one digest."""
    subject, body, count = await build_digest(session)
    to = _configured_recipient()

    if to is None:
        log.warning("digest not sent — no email channel configured")
        return {"sent": False, "count": count}

    try:
        await _send_email(to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 - surface the failure, don't raise
        log.error("digest failed", error=str(exc)[:2000])
        return {"sent": False, "count": count, "error": str(exc)[:2000]}

    log.info("digest sent", count=count, recipient=to)
    return {"sent": True, "count": count, "recipient": to}
