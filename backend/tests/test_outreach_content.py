"""Prospecting orchestration around email drafting.

Email *content* quality (no internal tags, natural prose, no repetition) is
the LLM's responsibility now — verified live against the real API in
app/services/outreach.py, not asserted deterministically here. These tests
cover what the code itself is responsible for: every contact gets a
personalized sequence, and a failed draft call degrades to a usable fallback
rather than breaking the whole sequence.
"""

from unittest.mock import AsyncMock, patch

from app.models import Account, Opportunity
from app.services.prospecting import LocalProspectingProvider


async def _build(session, *, account_name="Vehement Capital", signal_type="funding_financial"):
    account = Account(
        rox_entity_id=f"e-{account_name}", name=account_name, domain="vehement.com"
    )
    session.add(account)
    await session.flush()
    opp = Opportunity(
        account_id=account.id,
        title=f"{account_name}: Closed a $71M Series C",
        rationale="Vehement Capital raised $71M led by a growth fund.",
        qualification_score=90,
        signal_type=signal_type,
    )
    session.add(opp)
    await session.commit()

    provider = LocalProspectingProvider()
    contacts = await provider.find_contacts(account, opp)
    return account, opp, provider, contacts


class TestGeneratedEmails:
    async def test_contacts_use_default_personas(self, session):
        """signal_type is an open, model-generated vocabulary, so synthetic
        contacts use one generic persona set rather than per-signal targeting."""
        _, _, _, contacts = await _build(session)
        assert contacts[0].title == "VP of Engineering"
        assert all(c.match_reason for c in contacts)
        assert all("@vehement.com" in c.email for c in contacts)

    async def test_every_contact_gets_a_personalized_sequence(self, session):
        account, opp, provider, contacts = await _build(session)
        plan = await provider.build_sequence(account, opp, contacts)

        assert len(plan.emails) == len(contacts)
        for idx in range(len(contacts)):
            emails = plan.emails[idx]
            assert len(emails) == 2
            assert [e.step_number for e in emails] == [1, 2]
            assert all(e.subject and e.body for e in emails)

    async def test_falls_back_when_draft_call_fails(self, session):
        """A drafting failure degrades to a usable generic email, not a
        broken sequence — mirrors the rest of the codebase's "one failure
        must not kill the batch" pattern."""
        account, opp, provider, contacts = await _build(session)

        with patch("app.services.prospecting.draft_emails", AsyncMock(return_value=None)):
            plan = await provider.build_sequence(account, opp, contacts)

        assert len(plan.emails) == len(contacts)
        for emails in plan.emails.values():
            assert len(emails) == 2
            assert all(e.subject and e.body for e in emails)
