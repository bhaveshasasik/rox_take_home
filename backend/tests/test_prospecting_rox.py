"""Prospecting against the real Rox endpoints (/people, POST /sequences)."""

import httpx
import respx
from sqlalchemy import select

from app.models import Account, Contact, Opportunity, OutreachEmail, SequenceStatus
from app.rox.client import RoxClient
from app.services.prospecting import RoxProspectingProvider, run_prospecting

BASE = "https://rox.test"

#: shaped exactly like the live /people payload
PEOPLE = [
    {
        "rox_person_id": "p-hr",
        "name": "Keiaaron Majette",
        "title": "Human Resources Manager",
        "email": None,
        "linkedin_url": "https://linkedin.com/in/keiaaron",
        "seniority": None,
        "persona": "ANY",
    },
    {
        "rox_person_id": "p-cfo",
        "name": "Dana Reyes",
        "title": "Chief Financial Officer",
        "email": "dana.reyes@acme.com",
        "linkedin_url": "https://linkedin.com/in/dana",
        "seniority": None,
        "persona": "ANY",
    },
    {
        "rox_person_id": "p-vp",
        "name": "Sam Okafor",
        "title": "Vice President, Strategy",
        "email": "sam.okafor@acme.com",
        "linkedin_url": None,
        "seniority": None,
        "persona": "ANY",
    },
]


def mock_prospecting(people=None, sequence_fails: bool = False):
    router = respx.mock(base_url=BASE, assert_all_called=False)
    router.get("/people").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": PEOPLE if people is None else people,
                "total_count": len(PEOPLE if people is None else people),
                "next_page_id": None,
            },
        )
    )

    created: list[dict] = []

    def make_sequence(request):
        import json

        body = json.loads(request.content)
        created.append(body)
        if sequence_fails:
            return httpx.Response(400, json={"message": "nope"})
        return httpx.Response(
            201,
            json={
                "public_id": f"seq-{len(created)}",
                "status": "DRAFT",
                "rox_person_id": body.get("rox_person_id"),
                "sequence_tasks": [{"public_id": "task-1"}],
            },
        )

    router.post("/sequences").mock(side_effect=make_sequence)
    router.created = created
    return router


async def seed(session, signal_type="funding_financial"):
    account = Account(rox_entity_id="cust-1", name="Acme Corp", domain="acme.com")
    session.add(account)
    await session.flush()
    opp = Opportunity(
        account_id=account.id,
        title="Acme Corp: Closed a $71M Series C",
        rationale="Acme raised $71M led by a growth fund. https://example.com/pr",
        qualification_score=82,
        signal_type=signal_type,
    )
    session.add(opp)
    await session.commit()
    return account, opp


class TestRoxProvider:
    async def test_contacts_come_from_people_endpoint(self, session):
        account, opp = await seed(session)
        with mock_prospecting():
            async with RoxClient(base_url=BASE, token="t") as rox:
                contacts = await RoxProspectingProvider(rox).find_contacts(
                    account, opp, limit=3
                )

        assert len(contacts) == 3
        # no re-ranking: first in API order, not by seniority
        assert contacts[0].title == "Human Resources Manager"
        assert contacts[0].rox_contact_id == "p-hr"
        assert all(c.match_reason for c in contacts)

    async def test_creates_one_sequence_per_contact(self, session):
        account, opp = await seed(session)
        router = mock_prospecting()
        with router:
            async with RoxClient(base_url=BASE, token="t") as rox:
                sequence = await run_prospecting(
                    session, opp, RoxProspectingProvider(rox)
                )

        assert sequence.status == SequenceStatus.ACTIVE.value
        assert len(router.created) == 3, "one Rox sequence per contact"

        body = router.created[0]
        assert body["customer_id"] == "cust-1"
        assert body["rox_person_id"] in {"p-cfo", "p-vp", "p-hr"}
        # background_content must be an object, not a string
        assert isinstance(body["background_content"], dict)
        assert body["background_content"]["sources"] == ["https://example.com/pr"]

        task = body["sequence_tasks"][0]
        assert task["content"]["email_subject"]
        assert task["content"]["email_body"].startswith("<p>")
        assert len(task["scheduled_send_date"]) == 10  # yyyy-mm-dd

    async def test_real_person_ids_are_persisted(self, session):
        account, opp = await seed(session)
        with mock_prospecting():
            async with RoxClient(base_url=BASE, token="t") as rox:
                await run_prospecting(session, opp, RoxProspectingProvider(rox))

        contacts = (await session.execute(select(Contact))).scalars().all()
        assert {c.rox_contact_id for c in contacts} == {"p-cfo", "p-vp", "p-hr"}

    async def test_never_posts_a_fabricated_person_id(self, session):
        """rox_person_id is not FK-validated, so a missing id must be skipped
        rather than sent as a placeholder."""
        account, opp = await seed(session)
        router = mock_prospecting(people=[{"name": "Ghost", "title": "VP"}])
        with router:
            async with RoxClient(base_url=BASE, token="t") as rox:
                await run_prospecting(session, opp, RoxProspectingProvider(rox))

        assert router.created == [], "no sequence should be created without a real id"

    async def test_fails_explicitly_when_no_contacts(self, session):
        """No real contacts is a real state — surfaced as a failed sequence,
        never papered over with invented ones."""
        account, opp = await seed(session)
        with mock_prospecting(people=[]):
            async with RoxClient(base_url=BASE, token="t") as rox:
                sequence = await run_prospecting(
                    session, opp, RoxProspectingProvider(rox)
                )

        assert sequence.status == SequenceStatus.FAILED.value
        assert "no contacts found" in sequence.error
        contacts = (await session.execute(select(Contact))).scalars().all()
        assert contacts == []

    async def test_retries_a_failed_sequence_in_place(self, session):
        """A FAILED sequence must not permanently block prospecting — the
        manual re-run endpoint exists specifically to retry after a failure,
        and re-running with real contacts available should now succeed."""
        account, opp = await seed(session)
        with mock_prospecting(people=[]):
            async with RoxClient(base_url=BASE, token="t") as rox:
                first = await run_prospecting(session, opp, RoxProspectingProvider(rox))
        assert first.status == SequenceStatus.FAILED.value

        with mock_prospecting():
            async with RoxClient(base_url=BASE, token="t") as rox:
                retried = await run_prospecting(session, opp, RoxProspectingProvider(rox))

        assert retried.id == first.id, "retry reuses the same Sequence row, not a new one"
        assert retried.status == SequenceStatus.ACTIVE.value
        assert retried.error is None
        contacts = (await session.execute(select(Contact))).scalars().all()
        assert len(contacts) == 3, "no duplicate contacts left over from the failed attempt"

    async def test_does_not_retry_a_successful_sequence(self, session):
        account, opp = await seed(session)
        with mock_prospecting():
            async with RoxClient(base_url=BASE, token="t") as rox:
                first = await run_prospecting(session, opp, RoxProspectingProvider(rox))
        assert first.status == SequenceStatus.ACTIVE.value

        with mock_prospecting() as router:
            async with RoxClient(base_url=BASE, token="t") as rox:
                again = await run_prospecting(session, opp, RoxProspectingProvider(rox))

        assert again.id == first.id
        assert router.created == [], "an already-active sequence must not be re-run"

    async def test_sequence_failure_does_not_lose_local_state(self, session):
        account, opp = await seed(session)
        with mock_prospecting(sequence_fails=True):
            async with RoxClient(base_url=BASE, token="t") as rox:
                sequence = await run_prospecting(
                    session, opp, RoxProspectingProvider(rox)
                )

        # Rox rejected the writes, but the drafted emails are still reviewable
        assert sequence.status == SequenceStatus.ACTIVE.value
        assert (await session.execute(select(OutreachEmail))).scalars().all()
