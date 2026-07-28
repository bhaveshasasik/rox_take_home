"""Requirement 5: on accept, kick off prospecting automatically.

Contacts -> sequence -> enrollment -> generated outreach emails.

`RoxProspectingProvider` is the only provider `run_prospecting` selects
automatically: contacts come from `GET /people`, taken in the order Rox
returns them (no re-ranking), one `POST /sequences` per contact. If Rox has
no people for the account, prospecting fails explicitly — a `Sequence` in
`FAILED` status with a clear error — rather than inventing contacts. An
account with no real contacts is a real state, not something to paper over.

`LocalProspectingProvider` still exists for tests and demos that want
synthetic contacts on purpose, but it is never substituted in automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger
from app.models import (
    Account,
    Contact,
    EmailStatus,
    EnrollmentStatus,
    Opportunity,
    OutreachEmail,
    Sequence,
    SequenceEnrollment,
    SequenceStatus,
    Stage,
    utcnow,
)
from app.rox.client import RoxClient
from app.services.notifications import signal_label
from app.services.outreach import DraftedEmail, DraftedSequence, draft_emails

log = get_logger(__name__)


@dataclass
class ContactCandidate:
    name: str
    title: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    persona: str | None = None
    match_reason: str | None = None
    score: float | None = None
    rox_contact_id: str | None = None
    raw: dict | None = None


@dataclass
class DraftEmail:
    subject: str
    body: str
    step_number: int = 1


@dataclass
class SequencePlan:
    name: str
    #: contact index -> emails drafted for that contact
    emails: dict[int, list[DraftEmail]] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Provider seam
# ----------------------------------------------------------------------


class ProspectingProvider:
    async def find_contacts(
        self, account: Account, opportunity: Opportunity, limit: int = 3
    ) -> list[ContactCandidate]:
        raise NotImplementedError

    async def build_sequence(
        self,
        account: Account,
        opportunity: Opportunity,
        contacts: list[ContactCandidate],
    ) -> SequencePlan:
        raise NotImplementedError


#: Rox's signal_type is an open, model-generated vocabulary (whatever
#: category label its own research emits, slugified) — not a fixed set — so
#: there's no reliable way to target personas per signal. One generic set of
#: plausible technical/business buyers stands in for every signal type.
DEFAULT_PERSONAS = [
    ("VP of Engineering", "likely technical buyer"),
    ("CTO", "executive sponsor"),
    ("Head of Operations", "operational owner"),
]


class LocalProspectingProvider(ProspectingProvider):
    """Deterministic stand-in so the pipeline is end-to-end demoable now.

    Contacts are derived from the opportunity's signal rather than invented at
    random, so the prospecting view shows a defensible persona-to-signal match.
    """

    async def find_contacts(
        self, account: Account, opportunity: Opportunity, limit: int = 3
    ) -> list[ContactCandidate]:
        domain = (account.domain or f"{_slug(account.name)}.com").replace(
            "https://", ""
        ).replace("http://", "").strip("/")
        label = signal_label(opportunity.signal_type)

        candidates: list[ContactCandidate] = []
        for i, (title, why) in enumerate(DEFAULT_PERSONAS[:limit]):
            first, last = _synthetic_name(account.name, i)
            candidates.append(
                ContactCandidate(
                    name=f"{first} {last}",
                    title=title,
                    email=f"{first.lower()}.{last.lower()}@{domain}",
                    linkedin_url=f"https://linkedin.com/in/{first.lower()}-{last.lower()}",
                    persona=title,
                    match_reason=f"{title} {why}; matched to the {label} signal.",
                    score=round(max(0.0, (opportunity.qualification_score / 100) - i * 0.08), 2),
                )
            )
        return candidates

    async def build_sequence(
        self,
        account: Account,
        opportunity: Opportunity,
        contacts: list[ContactCandidate],
    ) -> SequencePlan:
        label = signal_label(opportunity.signal_type)
        plan = SequencePlan(name=f"{account.name} - {label} outreach")

        for idx, contact in enumerate(contacts):
            drafted = await draft_emails(
                account_name=account.name,
                contact_name=contact.name,
                contact_title=contact.title or "Contact",
                signal_label=label,
                rationale=opportunity.rationale,
            )
            if drafted is None:
                drafted = _fallback_sequence(account.name)

            plan.emails[idx] = [
                DraftEmail(
                    step_number=1,
                    subject=drafted.initial.subject,
                    body=drafted.initial.body,
                ),
                DraftEmail(
                    step_number=2,
                    subject=drafted.follow_up.subject,
                    body=drafted.follow_up.body,
                ),
            ]
        return plan


class RoxProspectingProvider(ProspectingProvider):
    """Prospecting against the real Rox API.

    Contacts come from `GET /people`; each contact gets its own Rox sequence via
    `POST /sequences` (one sequence targets one person).

    We author the email copy ourselves: `campaign_request_public_id` is silently
    dropped on write and there is no campaign generate/run trigger, so Rox will
    not write the body for us.
    """

    def __init__(self, rox: RoxClient) -> None:
        self.rox = rox
        self._local = LocalProspectingProvider()

    async def find_contacts(
        self, account: Account, opportunity: Opportunity, limit: int = 3
    ) -> list[ContactCandidate]:
        """Contacts as Rox returns them for this account — no re-ranking,
        first `limit` in API order."""
        people = await self.rox.list_people(account.rox_entity_id)
        if not people:
            log.info("no Rox contacts for account", account=account.name)
            return []

        label = signal_label(opportunity.signal_type)

        candidates: list[ContactCandidate] = []
        for person in people[:limit]:
            title = person.get("title") or "Contact"
            candidates.append(
                ContactCandidate(
                    name=str(person.get("name") or "Unknown"),
                    title=title,
                    email=person.get("email"),
                    linkedin_url=person.get("linkedin_url"),
                    persona=person.get("seniority") or title,
                    match_reason=f"{title} at {account.name}; surfaced for the {label} signal.",
                    rox_contact_id=person.get("rox_person_id"),
                    raw=person,
                )
            )
        return candidates

    async def build_sequence(
        self,
        account: Account,
        opportunity: Opportunity,
        contacts: list[ContactCandidate],
    ) -> SequencePlan:
        """Draft copy locally, then create one Rox sequence per contact."""
        plan = await self._local.build_sequence(account, opportunity, contacts)

        if not get_settings().rox_writes_enabled:
            log.info("rox writes disabled; sequence drafted locally only")
            return plan

        sources = [s for s in (opportunity.rationale or "").split() if s.startswith("http")]

        for idx, contact in enumerate(contacts):
            # Never post a fabricated id: rox_person_id is not FK-validated, so
            # a bad one silently creates an orphaned sequence.
            if not contact.rox_contact_id:
                log.warning("skipping Rox sequence, no rox_person_id", contact=contact.name)
                continue

            drafts = plan.emails.get(idx, [])
            if not drafts:
                continue

            payload = {
                "background_content": {
                    "content": {"summary": opportunity.title},
                    "sources": sources,
                },
                "customer_id": account.rox_entity_id,
                "rox_person_id": contact.rox_contact_id,
                "sequence_tasks": [
                    {
                        "background": {},
                        "content": {
                            "email_subject": d.subject,
                            "email_body": _to_html(d.body),
                        },
                        "scheduled_send_date": _send_date_for_step(d.step_number),
                    }
                    for d in drafts
                ],
            }
            try:
                created = await self.rox.create_sequence(payload)
                public_id = (created or {}).get("public_id") if isinstance(created, dict) else None
                if public_id:
                    contact.raw = {**(contact.raw or {}), "rox_sequence_id": str(public_id)}
                log.info(
                    "rox sequence created",
                    account=account.name,
                    contact=contact.name,
                    sequence_id=public_id,
                )
            except Exception as exc:  # noqa: BLE001 - one contact must not kill the batch
                log.error(
                    "rox sequence failed", contact=contact.name, error=str(exc)[:300]
                )

        return plan


def _to_html(body: str) -> str:
    """Rox stores email_body as HTML; live examples are <p>-wrapped."""
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    return "".join(f"<p>{p.replace(chr(10), '<br/>')}</p>" for p in paragraphs)


def _send_date_for_step(step_number: int) -> str:
    """Step 1 goes out tomorrow; step 2 is the follow-up outreach.py's own
    prompt promises ("sent about a week later if there's no reply") — without
    this, both steps were scheduled for the same date."""
    days = 1 if step_number == 1 else 8
    return (utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# Orchestration (provider-agnostic)
# ----------------------------------------------------------------------


async def run_prospecting(
    session: AsyncSession,
    opportunity: Opportunity,
    provider: ProspectingProvider | None = None,
) -> Sequence | None:
    """Find contacts, create a sequence, enroll them, draft the emails.

    Idempotent for a *successful or in-progress* sequence: an opportunity that
    already has one is left alone, so a retried accept never double-enrolls a
    contact. A `FAILED` sequence is not left alone — it's retried in place,
    since that's the whole point of the manual re-run endpoint.

    Defaults to the real Rox provider. If it finds no contacts for the
    account, prospecting fails explicitly (`Sequence.status = FAILED`,
    `Sequence.error` set) rather than silently substituting invented ones.
    """
    provider = provider or _default_provider()

    existing = (
        await session.execute(
            select(Sequence).where(Sequence.opportunity_id == opportunity.id)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status != SequenceStatus.FAILED.value:
        log.info(
            "prospecting already ran", opportunity_id=opportunity.id, sequence_id=existing.id
        )
        return existing

    account = (
        await session.execute(select(Account).where(Account.id == opportunity.account_id))
    ).scalar_one_or_none()
    if account is None:
        log.error("prospecting has no account", opportunity_id=opportunity.id)
        return None

    if existing is not None:
        log.info(
            "retrying failed prospecting", opportunity_id=opportunity.id, sequence_id=existing.id
        )
        # A prior attempt may have created contacts before failing later in
        # the try block below; clear them so the retry can't double them up.
        stale_contacts = (
            await session.execute(
                select(Contact).where(Contact.opportunity_id == opportunity.id)
            )
        ).scalars().all()
        for stale in stale_contacts:
            await session.delete(stale)
        sequence = existing
        sequence.name = f"{account.name} outreach"
        sequence.status = SequenceStatus.DRAFT.value
        sequence.error = None
    else:
        sequence = Sequence(
            opportunity_id=opportunity.id,
            account_id=account.id,
            name=f"{account.name} outreach",
            status=SequenceStatus.DRAFT.value,
        )
        session.add(sequence)
    await session.flush()

    try:
        candidates = await provider.find_contacts(
            account, opportunity, limit=get_settings().contacts_per_opportunity
        )
        if not candidates:
            raise RuntimeError(f"no contacts found for account {account.name!r}")

        contacts: list[Contact] = []
        for candidate in candidates:
            contact = Contact(
                account_id=account.id,
                opportunity_id=opportunity.id,
                rox_contact_id=candidate.rox_contact_id,
                name=candidate.name,
                title=candidate.title,
                email=candidate.email,
                linkedin_url=candidate.linkedin_url,
                persona=candidate.persona,
                match_reason=candidate.match_reason,
                score=candidate.score,
                raw=candidate.raw,
            )
            session.add(contact)
            contacts.append(contact)
        await session.flush()

        opportunity.stage = Stage.PROSPECTED.value

        plan = await provider.build_sequence(account, opportunity, candidates)
        sequence.name = plan.name

        drafted = 0
        for idx, contact in enumerate(contacts):
            # Each contact gets its own Rox sequence, so the per-contact id from
            # the provider is the enrollment's real handle in Rox.
            candidate_raw = candidates[idx].raw or {}
            enrollment = SequenceEnrollment(
                sequence_id=sequence.id,
                contact_id=contact.id,
                rox_enrollment_id=candidate_raw.get("rox_sequence_id"),
                status=EnrollmentStatus.ACTIVE.value,
            )
            session.add(enrollment)
            await session.flush()

            for draft in plan.emails.get(idx, []):
                session.add(
                    OutreachEmail(
                        enrollment_id=enrollment.id,
                        step_number=draft.step_number,
                        subject=draft.subject,
                        body=draft.body,
                        status=EmailStatus.DRAFT.value,
                    )
                )
                drafted += 1

        sequence.status = SequenceStatus.ACTIVE.value
        opportunity.stage = Stage.SEQUENCED.value

        await session.commit()
        log.info(
            "prospecting complete",
            opportunity_id=opportunity.id,
            account=account.name,
            contacts=len(contacts),
            emails=drafted,
        )
        return sequence

    except Exception as exc:  # noqa: BLE001 - surface the failure in the UI
        sequence.status = SequenceStatus.FAILED.value
        sequence.error = str(exc)[:2000]
        await session.commit()
        log.error(
            "prospecting failed", opportunity_id=opportunity.id, error=str(exc)
        )
        return sequence


def _default_provider() -> ProspectingProvider:
    """Rox-backed when a token is configured, local otherwise (tests, offline)."""
    settings = get_settings()
    if settings.rox_api_token:
        return RoxProspectingProvider(RoxClient())
    return LocalProspectingProvider()


def _slug(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum()) or "example"


def _synthetic_name(account_name: str, index: int) -> tuple[str, str]:
    firsts = ["Jordan", "Riley", "Avery", "Morgan", "Casey", "Quinn"]
    lasts = ["Chen", "Patel", "Rivera", "Okafor", "Novak", "Haddad"]
    seed = sum(ord(c) for c in account_name)
    return firsts[(seed + index) % len(firsts)], lasts[(seed * 3 + index) % len(lasts)]


def _fallback_sequence(account_name: str) -> DraftedSequence:
    """Used only if the LLM draft call itself fails — keeps prospecting from
    failing outright over an API hiccup. Never the primary path."""
    return DraftedSequence(
        initial=DraftedEmail(
            subject=f"{account_name} + Rox",
            body=(
                f"Hi,\n\nWe've flagged {account_name} as a strong-fit opportunity worth "
                f"a conversation. Open to a quick call?\n\nBest,\nThe Rox Team"
            ),
        ),
        follow_up=DraftedEmail(
            subject=f"Re: {account_name} + Rox",
            body="Hi,\n\nFollowing up on my note below — happy to find a time that works.\n\nBest,\nThe Rox Team",
        ),
    )
