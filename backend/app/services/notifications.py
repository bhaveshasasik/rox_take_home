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
    Digest,
    DigestOpportunity,
    Notification,
    NotificationStatus,
    Opportunity,
    OpportunityStatus,
    Stage,
    utcnow,
)
from app.services.rationale import write_notification_summary
from app.signals.schema import signal_label as _signal_label

log = get_logger(__name__)


def signal_label(signal_type: str) -> str:
    """Human-readable label for a signal_type.

    Now a lookup against the fixed `SignalType` vocabulary. The old open
    vocabulary — slugified from whatever category label Rox happened to emit —
    produced `trigger_event` and `trigger_events` as separate values plus four
    rows where an entire sentence became the slug.

    The title-cased fallback remains for rows written before the backfill: a
    label is display text, and rendering a legacy value badly beats rendering
    it blank.
    """
    return _signal_label(signal_type)


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
        # A resend must never overwrite the original delivery time —
        # decision latency is measured from it.
        opportunity.notified_at = opportunity.notified_at or utcnow()
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

        # a def rather than a lambda: it is a coroutine function, and binding
        # `to` here keeps the call site identical to a caller-supplied sender
        async def send(m: OpportunityMessage) -> dict:
            return await _default_send(to, m)

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
    """The block for one opportunity in the digest.

    Account, primary signal label, score, review link — structured values
    only. This is the last place generated prose could reach a user, and it
    does not need to: the label is the scoring driver (stored at creation,
    kept fresh by rescore), and the score is the number the reviewer will see
    in the queue. No LLM output appears in a digest.
    """
    return [
        "",
        f"{account_name} — {signal_label(opportunity.signal_type)} — "
        f"{opportunity.qualification_score}/100",
        f"Review: {base_url}/opportunities/{opportunity.id}",
    ]


# ----------------------------------------------------------------------
# Digest eligibility — the one query every send path shares
# ----------------------------------------------------------------------


async def digest_eligible(session: AsyncSession) -> list[tuple[Opportunity, str]]:
    """Opportunities a digest may include, strongest first.

    The single definition of eligible: at or above the notification score
    floor, still undecided, and not a member of any prior digest that was —
    or still may be — delivered. Every send path reads this; none builds its
    own query, because two paths with two queries is how the same
    opportunity gets emailed twice.

    Membership in a *failed* digest does not consume eligibility: nothing
    reached the reviewer, so the rows belong in the next attempt. Pending
    does consume it — digest rows are written before the send, per the same
    attempt-before-send rule Notifications follow, and a digest stuck in
    pending is an operator-visible problem rather than a license to double
    send.
    """
    settings = get_settings()

    consumed = (
        select(DigestOpportunity.opportunity_id)
        .join(Digest, Digest.id == DigestOpportunity.digest_id)
        .where(Digest.status != NotificationStatus.FAILED.value)
    )

    rows = await session.execute(
        select(Opportunity, Account.name)
        .join(Account, Opportunity.account_id == Account.id)
        .where(
            Opportunity.status == OpportunityStatus.NEW.value,
            Opportunity.qualification_score >= settings.notification_score_threshold,
            Opportunity.id.not_in(consumed),
        )
        .order_by(
            Opportunity.qualification_score.desc(),
            Opportunity.created_at,
            Opportunity.id,
        )
    )
    return [(opp, name) for opp, name in rows.all()]


# ----------------------------------------------------------------------
# Digest — every eligible opportunity, one email per producing run
# ----------------------------------------------------------------------


def build_digest(
    rows: list[tuple[Opportunity, str]], base_url: str
) -> tuple[str, str]:
    """Return (subject, body) for the given eligible opportunities.

    Pure formatting over rows the caller already selected — eligibility lives
    in `digest_eligible`, nowhere else. There is no empty-state message
    because an empty digest is never sent: no qualifying opportunities means
    no email, not a friendlier email.
    """
    settings = get_settings()
    lines = ["Today's Opportunities", "-" * 21, ""]
    lines.append(f"{len(rows)} account{'s' if len(rows) != 1 else ''} ready for your review.")
    for opp, account_name in rows:
        lines.extend(_entry_lines(opp, account_name, base_url))

    # The queue itself, pre-filtered to what this digest describes: pending
    # review at or above the notification floor, strongest first.
    lines.extend(
        [
            "",
            "Full queue: "
            f"{base_url}/?status=new&min_score={settings.notification_score_threshold}"
            "&sort=score&order=desc",
        ]
    )

    body = "\n".join(lines).rstrip() + "\n"
    subject = f"[Rox Pipeline] {len(rows)} opportunit{'ies' if len(rows) != 1 else 'y'} to review today"
    return subject, body


async def send_digest(
    session: AsyncSession,
    *,
    trigger: str = "manual",
    run_id: str | None = None,
) -> dict:
    """Email every digest-eligible opportunity as one message, with a record.

    Writes the Digest row and its memberships *before* sending — the same
    attempt-before-send rule Notification rows follow, and the membership is
    what makes running this twice in a row send nothing the second time.
    """
    settings = get_settings()
    eligible = await digest_eligible(session)

    if not eligible:
        log.info("digest skipped — nothing eligible")
        return {"sent": False, "count": 0}

    to = _configured_recipient()
    if to is None:
        log.warning("digest not sent — no email channel configured")
        return {"sent": False, "count": len(eligible)}

    digest = Digest(
        channel=Channel.EMAIL.value,
        recipient=to,
        trigger=trigger,
        run_id=run_id,
        status=NotificationStatus.PENDING.value,
    )
    session.add(digest)
    await session.flush()
    records: list[Notification] = []
    for opp, _name in eligible:
        session.add(DigestOpportunity(digest_id=digest.id, opportunity_id=opp.id))
        # One Notification row per included opportunity, like every other
        # send path — the detail view's timeline and /notifications read
        # these, and an opportunity notified by digest must not look
        # un-notified there.
        record = Notification(
            opportunity_id=opp.id,
            channel=Channel.EMAIL.value,
            recipient=to,
            status=NotificationStatus.PENDING.value,
        )
        session.add(record)
        records.append(record)
    await session.flush()

    subject, body = build_digest(eligible, settings.app_base_url.rstrip("/"))

    try:
        await _send_email(to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 - surface the failure, don't raise
        digest.status = NotificationStatus.FAILED.value
        digest.error = str(exc)[:2000]
        for record in records:
            record.status = NotificationStatus.FAILED.value
            record.error = str(exc)[:2000]
        await session.commit()
        log.error("digest failed", error=str(exc)[:2000])
        return {"sent": False, "count": len(eligible), "error": str(exc)[:2000]}

    now = utcnow()
    digest.status = NotificationStatus.SENT.value
    digest.sent_at = now
    for record, (opp, _name) in zip(records, eligible):
        record.status = NotificationStatus.SENT.value
        record.sent_at = now
        # Decision latency is timed from notified_at; a resend must never
        # overwrite the first delivery.
        opp.notified_at = opp.notified_at or now
        if opp.stage == Stage.OPPORTUNITY_CREATED.value:
            opp.stage = Stage.NOTIFIED.value
    await session.commit()
    log.info("digest sent", count=len(eligible), recipient=to, digest_id=digest.id)
    return {
        "sent": True,
        "count": len(eligible),
        "recipient": to,
        "digest_id": digest.id,
    }
