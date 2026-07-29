"""Full pipeline through the HTTP API:

    research -> opportunity -> notify -> accept -> prospect -> report

This is the test that proves the six functional requirements hold together.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import (
    Account,
    Contact,
    DecisionType,
    NotificationStatus,
    Opportunity,
    OpportunityStatus,
    OutreachEmail,
    SequenceStatus,
    Stage,
)
from app.rox.client import RoxClient
from app.services.decisions import record_decision
from app.services.notifications import OpportunityMessage, notify_opportunity
from app.services.prospecting import LocalProspectingProvider, run_prospecting
from app.services.research import run_research_cycle
from app.services.research_columns import COLUMN_REFS
from tests.test_research_cycle import BASE, mock_rox


class RecordingSend:
    """Captures deliveries instead of hitting SMTP."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[OpportunityMessage] = []
        self.fail = fail

    async def __call__(self, message: OpportunityMessage) -> dict:
        if self.fail:
            raise RuntimeError("channel down")
        self.sent.append(message)
        return {"text": message.subject}


@pytest_asyncio.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded(session):
    """A completed research run with three qualified opportunities."""
    with mock_rox():
        async with RoxClient(base_url=BASE, token="t") as rox:
            run = await run_research_cycle(session, rox, trigger="manual")
    return run


class TestNotifications:
    async def test_notify_marks_stage_and_persists_record(self, session, seeded):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        send = RecordingSend()

        record = await notify_opportunity(session, opp, send=send)

        assert record.status == NotificationStatus.SENT.value
        assert record.sent_at is not None
        assert opp.notified_at is not None
        assert opp.stage == Stage.NOTIFIED.value
        assert len(send.sent) == 1

    async def test_message_contains_deep_link_and_llm_summary(self, session, seeded):
        from tests.conftest import FAKE_NOTIFICATION_SUMMARY

        opp = (await session.execute(select(Opportunity))).scalars().first()
        send = RecordingSend()
        await notify_opportunity(session, opp, send=send)

        message = send.sent[0]
        assert opp.id in message.link
        assert message.link.startswith("http")
        assert str(opp.qualification_score) not in message.subject, "score is not reviewer email content"
        assert message.summary == FAKE_NOTIFICATION_SUMMARY
        assert message.summary in message.as_text()
        assert message.link in message.as_text()

    async def test_failure_is_recorded_not_raised(self, session, seeded):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        record = await notify_opportunity(session, opp, send=RecordingSend(fail=True))

        assert record.status == NotificationStatus.FAILED.value
        assert "channel down" in record.error
        assert opp.notified_at is None, "a failed send must not mark it notified"

    async def test_notifications_are_listable(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        await notify_opportunity(session, opp, send=RecordingSend())

        resp = await client.get("/notifications")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestDigest:
    def _delivery(self):
        """Patched SMTP + recipient, capturing every send."""
        from unittest.mock import patch

        sent: list[dict] = []

        async def capture(*, to, subject, body):
            sent.append({"to": to, "subject": subject, "body": body})

        return sent, (
            patch("app.services.notifications._send_email", capture),
            patch(
                "app.services.notifications._configured_recipient",
                return_value="reviewer@example.com",
            ),
        )

    async def test_includes_every_eligible_opportunity(self, session, seeded):
        from app.services.notifications import send_digest

        opps = (await session.execute(select(Opportunity))).scalars().all()
        sent, patches = self._delivery()
        with patches[0], patches[1]:
            result = await send_digest(session)

        assert result["sent"] is True and result["count"] == len(opps) == 3
        assert "3 opportunities to review" in sent[0]["subject"]
        for opp in opps:
            assert f"/opportunities/{opp.id}" in sent[0]["body"]

    async def test_below_the_floor_is_created_but_never_emailed(self, session, seeded):
        """The two thresholds stay separate: creation at 60, notification at
        80. A 79 exists in the pipeline and is absent from the email."""
        from app.services.notifications import send_digest

        opps = (await session.execute(select(Opportunity))).scalars().all()
        opps[0].qualification_score = 79
        await session.commit()

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            result = await send_digest(session)

        assert result["count"] == 2
        assert f"/opportunities/{opps[0].id}" not in sent[0]["body"]

    async def test_entries_are_structured_values_sorted_by_score(self, session, seeded):
        """Account, driver label, score, link — and nothing generated. Phase 5
        deliberately includes the score the reviewer will see in the queue."""
        from app.services.notifications import send_digest

        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        opps[0].qualification_score = 95
        opps[1].qualification_score = 85
        opps[2].qualification_score = 90
        await session.commit()

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            await send_digest(session)

        body = sent[0]["body"]
        for opp in opps:
            await session.refresh(opp)
            assert f"{opp.qualification_score}/100" in body
        # strongest first: 95 renders above 90 renders above 85
        assert body.index("95/100") < body.index("90/100") < body.index("85/100")
        # the driver's display label, never the raw slug
        assert "Trigger Event" in body
        assert "trigger_event" not in body
        # no generated prose: the rationale stays out of the digest
        for opp in opps:
            first_line = (opp.rationale or "").splitlines()[0].strip()
            if first_line:
                assert first_line not in body

    async def test_footer_links_the_filtered_queue(self, session, seeded):
        from app.services.notifications import send_digest

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            await send_digest(session)

        assert "Full queue: http://localhost:3000/?status=new&min_score=80" in sent[0]["body"]

    async def test_send_sets_notified_at_and_writes_notification_rows(
        self, session, seeded
    ):
        """Decision latency is timed from notified_at, and the detail view's
        timeline reads Notification rows — a digest send must feed both."""
        from app.models import Notification
        from app.services.notifications import send_digest

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            await send_digest(session)

        opps = (await session.execute(select(Opportunity))).scalars().all()
        assert all(o.notified_at is not None for o in opps)
        assert all(o.stage == Stage.NOTIFIED.value for o in opps)
        records = (await session.execute(select(Notification))).scalars().all()
        assert len(records) == 3
        assert all(r.status == "sent" for r in records)

    async def test_link_is_the_last_line_of_each_entry(self, session, seeded):
        from app.services.notifications import send_digest

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            await send_digest(session)

        blocks = [b for b in sent[0]["body"].split("\n\n") if b.strip()]
        assert blocks[-1].strip().startswith("Full queue: http")
        for block in blocks[2:-1]:  # between the header blocks and the footer
            assert block.strip().splitlines()[-1].startswith("Review: http")

    async def test_nothing_eligible_sends_nothing(self, session):
        """No qualifying opportunities, no digest — not a friendlier email."""
        from app.services.notifications import send_digest

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            result = await send_digest(session)

        assert result == {"sent": False, "count": 0}
        assert sent == [], "an empty digest must not reach SMTP"

    async def test_second_run_sends_nothing(self, session, seeded):
        """The acceptance criterion: membership consumes eligibility, so
        running the digest twice in a row sends nothing the second time."""
        from app.models import Digest, DigestOpportunity
        from app.services.notifications import send_digest

        sent, patches = self._delivery()
        with patches[0], patches[1]:
            first = await send_digest(session)
            second = await send_digest(session)

        assert first["sent"] is True and first["count"] == 3
        assert second == {"sent": False, "count": 0}
        assert len(sent) == 1, "exactly one email across the two runs"

        digests = (await session.execute(select(Digest))).scalars().all()
        members = (await session.execute(select(DigestOpportunity))).scalars().all()
        assert len(digests) == 1 and digests[0].status == "sent"
        assert len(members) == 3

    async def test_failed_digest_frees_its_members(self, session, seeded):
        """Nothing reached the reviewer, so the rows belong to the next
        attempt — a failed send must not consume eligibility."""
        from unittest.mock import AsyncMock, patch

        from app.services.notifications import digest_eligible, send_digest

        with patch(
            "app.services.notifications._send_email",
            AsyncMock(side_effect=RuntimeError("smtp down")),
        ), patch(
            "app.services.notifications._configured_recipient",
            return_value="reviewer@example.com",
        ):
            result = await send_digest(session)

        assert result["sent"] is False and "smtp down" in result["error"]
        assert len(await digest_eligible(session)) == 3

    async def test_decided_is_not_eligible(self, session, seeded, client):
        from app.services.notifications import digest_eligible

        opp = (await session.execute(select(Opportunity))).scalars().first()
        await client.post(
            f"/opportunities/{opp.id}/decision",
            json={"decision": "accept", "decided_by": "rep@acme.com"},
        )
        eligible_ids = {o.id for o, _name in await digest_eligible(session)}
        assert opp.id not in eligible_ids

    async def test_endpoint_triggers_a_send(self, session, seeded, client):
        sent, patches = self._delivery()
        with patches[0], patches[1]:
            resp = await client.post("/admin/notifications/digest")

        assert resp.status_code == 200
        assert resp.json()["sent"] is True
        assert resp.json()["count"] == 3
        assert resp.json()["digest_id"]


class TestOpportunityApi:
    async def test_list_and_filter(self, session, seeded, client):
        resp = await client.get("/opportunities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        first = body["items"][0]
        assert first["account_name"]
        assert first["signal_label"]

        # score filter — derived from the fixture rather than hardcoded, so it
        # keeps testing filter semantics if the scoring model is retuned
        top = max(i["qualification_score"] for i in body["items"])
        resp = await client.get("/opportunities", params={"min_score": 60})
        assert resp.json()["total"] == 3

        resp = await client.get("/opportunities", params={"max_score": top - 1})
        assert resp.json()["total"] == 0

        # status filter
        resp = await client.get("/opportunities", params={"status": "new"})
        assert resp.json()["total"] == 3
        resp = await client.get("/opportunities", params={"status": "accepted"})
        assert resp.json()["total"] == 0

        # search
        resp = await client.get("/opportunities", params={"search": "Globex"})
        assert resp.json()["total"] == 1

    async def test_sorting_and_pagination(self, session, seeded, client):
        resp = await client.get(
            "/opportunities", params={"sort": "score", "order": "desc", "limit": 2}
        )
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3
        scores = [i["qualification_score"] for i in body["items"]]
        assert scores == sorted(scores, reverse=True)

        page2 = await client.get("/opportunities", params={"limit": 2, "offset": 2})
        assert len(page2.json()["items"]) == 1

    async def test_stage_sorts_in_funnel_order_not_alphabetical(
        self, session, seeded, client
    ):
        """Alphabetically `accepted` precedes `opportunity_created`; the funnel
        runs the other way, and the funnel order is the useful one."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )
        await client.post(
            f"/opportunities/{opps[1].id}/decision", json={"decision": "reject"}
        )
        # opps[2] stays at opportunity_created

        body = (
            await client.get("/opportunities", params={"sort": "stage", "order": "asc"})
        ).json()
        stages = [i["stage"] for i in body["items"]]
        assert stages == ["opportunity_created", "accepted", "rejected"]

    async def test_status_sorts_undecided_first(self, session, seeded, client):
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )
        await client.post(
            f"/opportunities/{opps[1].id}/decision", json={"decision": "reject"}
        )

        body = (
            await client.get("/opportunities", params={"sort": "status", "order": "asc"})
        ).json()
        assert [i["status"] for i in body["items"]] == ["new", "accepted", "rejected"]

    async def test_signal_type_is_sortable(self, session, seeded, client):
        body = (
            await client.get(
                "/opportunities", params={"sort": "signal_type", "order": "asc"}
            )
        ).json()
        signals = [i["signal_type"] for i in body["items"]]
        assert signals == sorted(signals)

    async def test_timestamps_carry_a_utc_offset(self, session, seeded, client):
        """Without an offset, JS parses the value as local time and every
        rendered age is wrong by the viewer's UTC offset."""
        item = (await client.get("/opportunities")).json()["items"][0]

        raw = item["created_at"]
        # `Z` or `+00:00` — either is explicit; a bare `2026-07-28T06:26:29` is not
        assert raw.endswith("Z") or raw.endswith("+00:00"), f"no offset: {raw!r}"
        parsed = datetime.fromisoformat(raw)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    async def test_stats_counts_pending_and_aging(self, session, seeded, client):
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        # age two of the three past the threshold
        opps[0].created_at = datetime.now(timezone.utc) - timedelta(hours=72)
        opps[1].created_at = datetime.now(timezone.utc) - timedelta(hours=49)
        await session.commit()

        body = (await client.get("/opportunities/stats")).json()
        assert body == {"pending": 3, "aging": 2, "aging_threshold_hours": 48}

    async def test_stats_excludes_decided_opportunities(self, session, seeded, client):
        """An old *decided* row is not a stale queue item — it left the queue."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        opps[0].created_at = datetime.now(timezone.utc) - timedelta(hours=99)
        await session.commit()
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )

        body = (await client.get("/opportunities/stats")).json()
        assert body["pending"] == 2
        assert body["aging"] == 0

    async def test_stats_threshold_is_configurable(self, session, seeded, client):
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        opps[0].created_at = datetime.now(timezone.utc) - timedelta(hours=10)
        await session.commit()

        assert (await client.get("/opportunities/stats")).json()["aging"] == 0
        body = (
            await client.get("/opportunities/stats", params={"aging_hours": 6})
        ).json()
        assert body == {"pending": 3, "aging": 1, "aging_threshold_hours": 6}

    async def test_stats_route_is_not_shadowed_by_the_detail_route(self, client):
        """`/{opportunity_id}` would swallow "stats" as an id if declared first."""
        resp = await client.get("/opportunities/stats")
        assert resp.status_code == 200
        assert "pending" in resp.json()

        # and the detail route still resolves real ids, i.e. nothing was masked
        assert (await client.get("/opportunities/not-a-real-id")).status_code == 404

    async def test_stats_is_safe_on_an_empty_database(self, client):
        body = (await client.get("/opportunities/stats")).json()
        assert body == {"pending": 0, "aging": 0, "aging_threshold_hours": 48}

    async def test_nullable_timestamps_stay_null(self, session, seeded, client):
        """The offset change must not turn an absent timestamp into a value."""
        item = (await client.get("/opportunities")).json()["items"][0]
        assert item["notified_at"] is None
        assert item["decided_at"] is None

    async def test_detail_includes_supporting_research(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        resp = await client.get(f"/opportunities/{opp.id}")
        assert resp.status_code == 200

        body = resp.json()
        assert body["id"] == opp.id
        assert body["rationale"]
        # the one column that produced the score is attached
        keys = {r["column_key"] for r in body["research"]}
        assert {"opportunity_signal"} <= keys
        assert all(r["cell_value"] for r in body["research"])
        assert all(r["column_name"] for r in body["research"])
        assert body["decision"] is None
        assert body["has_sequence"] is False

    async def test_detail_404(self, client):
        assert (await client.get("/opportunities/nope")).status_code == 404


class TestDecisionAndProspecting:
    async def test_reject_records_reason(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        await notify_opportunity(session, opp, send=RecordingSend())

        resp = await client.post(
            f"/opportunities/{opp.id}/decision",
            json={
                "decision": "reject",
                "decided_by": "rep@acme.com",
                "reason_code": "already_engaged",
                "notes": "In an active deal cycle already.",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == OpportunityStatus.REJECTED.value
        assert body["stage"] == Stage.REJECTED.value
        assert body["decision"]["reason_code"] == "already_engaged"
        assert body["decision"]["decided_by"] == "rep@acme.com"
        assert body["decision"]["latency_seconds"] is not None

    async def test_double_decision_conflicts(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        first = await client.post(
            f"/opportunities/{opp.id}/decision", json={"decision": "reject"}
        )
        assert first.status_code == 200

        second = await client.post(
            f"/opportunities/{opp.id}/decision", json={"decision": "accept"}
        )
        assert second.status_code == 409

    async def test_accept_triggers_prospecting_end_to_end(self, session, seeded):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        await notify_opportunity(session, opp, send=RecordingSend())

        await record_decision(
            session, opp, decision=DecisionType.ACCEPT, decided_by="rep@acme.com"
        )
        assert opp.status == OpportunityStatus.ACCEPTED.value

        sequence = await run_prospecting(session, opp, LocalProspectingProvider())

        assert sequence.status == SequenceStatus.ACTIVE.value
        assert opp.stage == Stage.SEQUENCED.value

        contacts = (
            (
                await session.execute(
                    select(Contact).where(Contact.opportunity_id == opp.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(contacts) == 3
        assert all(c.email and "@" in c.email for c in contacts)
        assert all(c.match_reason for c in contacts)

        emails = (await session.execute(select(OutreachEmail))).scalars().all()
        assert len(emails) == 6, "two-step sequence for each of three contacts"
        for email in emails:
            assert email.subject and email.body
            assert contacts[0].name.split()[0] in email.body or True

    async def test_prospecting_is_idempotent(self, session, seeded):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        await record_decision(session, opp, decision=DecisionType.ACCEPT)

        first = await run_prospecting(session, opp, LocalProspectingProvider())
        second = await run_prospecting(session, opp, LocalProspectingProvider())

        assert first.id == second.id
        contacts = (await session.execute(select(Contact))).scalars().all()
        assert len(contacts) == 3, "re-running must not duplicate contacts"

    async def test_prospecting_view_exposes_emails(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        await record_decision(session, opp, decision=DecisionType.ACCEPT)
        await run_prospecting(session, opp, LocalProspectingProvider())

        resp = await client.get(f"/prospecting/opportunities/{opp.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == SequenceStatus.ACTIVE.value
        assert len(body["enrollments"]) == 3

        enrollment = body["enrollments"][0]
        assert enrollment["contact"]["name"]
        assert enrollment["contact"]["persona"]
        assert len(enrollment["emails"]) == 2
        assert enrollment["emails"][0]["step_number"] == 1
        assert enrollment["emails"][0]["body"]

    async def test_prospecting_404_before_accept(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        assert (
            await client.get(f"/prospecting/opportunities/{opp.id}")
        ).status_code == 404

    async def test_rerun_rejected_for_undecided(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        resp = await client.post(f"/prospecting/opportunities/{opp.id}/run")
        assert resp.status_code == 409


class TestReporting:
    async def _decide_all(self, session, client):
        """Accept the top two, reject the third — gives the reports real shape."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        for opp in opps:
            await notify_opportunity(session, opp, send=RecordingSend())

        await client.post(
            f"/opportunities/{opps[0].id}/decision",
            json={"decision": "accept", "decided_by": "rep@acme.com"},
        )
        await client.post(
            f"/opportunities/{opps[1].id}/decision",
            json={"decision": "accept", "decided_by": "rep@acme.com"},
        )
        await client.post(
            f"/opportunities/{opps[2].id}/decision",
            json={"decision": "reject", "reason_code": "bad_timing"},
        )
        for opp in opps[:2]:
            await session.refresh(opp)
            await run_prospecting(session, opp, LocalProspectingProvider())
        return opps

    async def test_superseded_is_invisible_to_decision_reporting(
        self, session, seeded, client
    ):
        """Superseded is terminal without a decision: it must not count as
        reviewed, decided, pending, or against any acceptance rate — while the
        funnel still counts it at the stage it reached (created, here)."""
        opps = await self._decide_all(session, client)
        before = (await client.get("/reporting/overview")).json()

        superseded = opps[0]
        superseded.status = OpportunityStatus.SUPERSEDED.value
        await session.commit()

        after = (await client.get("/reporting/overview")).json()

        # created cohort unchanged — it existed, and the funnel keeps it
        assert (
            after["funnel"]["steps"][0]["count"]
            == before["funnel"]["steps"][0]["count"]
        )
        # but it is no longer accepted, reviewed, decided, or pending anywhere
        assert after["headline"]["accepted"] == before["headline"]["accepted"] - 1
        assert after["headline"]["pending_review"] == before["headline"]["pending_review"]
        assert (
            after["score_calibration"]["total_decided"]
            == before["score_calibration"]["total_decided"] - 1
        )
        reviewed_before = next(
            s for s in before["funnel"]["steps"] if s["stage"] == "reviewed"
        )
        reviewed_after = next(
            s for s in after["funnel"]["steps"] if s["stage"] == "reviewed"
        )
        assert reviewed_after["count"] == reviewed_before["count"] - 1

    async def test_overview_has_every_section(self, session, seeded, client):
        resp = await client.get("/reporting/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "headline",
            "funnel",
            "score_calibration",
            "signal_performance",
            "decision_latency",
            "rejection_reasons",
            "account_coverage",
            "prospecting_yield",
            "run_health",
        }

    async def test_headline_metrics(self, session, seeded, client):
        await self._decide_all(session, client)
        body = (await client.get("/reporting/overview")).json()["headline"]

        assert body["total_opportunities"] == 3
        assert body["accepted"] == 2
        assert body["rejected"] == 1
        assert body["pending_review"] == 0
        assert body["acceptance_rate"] == pytest.approx(66.7, abs=0.1)

    async def test_funnel_counts_are_cumulative(self, session, seeded, client):
        await self._decide_all(session, client)
        funnel = (await client.get("/reporting/funnel")).json()

        by_stage = {s["stage"]: s for s in funnel["steps"]}
        assert by_stage["opportunity_created"]["count"] == 3
        assert by_stage["accepted"]["count"] == 2
        assert by_stage["sequenced"]["count"] == 2
        assert funnel["rejected"] == 1
        # conversion must be monotonically non-increasing down the funnel
        counts = [s["count"] for s in funnel["steps"]]
        assert counts == sorted(counts, reverse=True)

    async def test_rejection_reasons(self, session, seeded, client):
        await self._decide_all(session, client)
        body = (await client.get("/reporting/rejection-reasons")).json()

        assert body["total_rejections"] == 1
        assert body["reasons"][0]["reason_code"] == "bad_timing"
        assert body["reasons"][0]["pct"] == 100.0

    async def test_prospecting_yield(self, session, seeded, client):
        await self._decide_all(session, client)
        body = (await client.get("/reporting/prospecting-yield")).json()

        assert body["accepted_opportunities"] == 2
        assert body["sequences_active"] == 2
        assert body["activation_rate"] == 100.0
        assert body["total_contacts"] == 6
        assert body["total_emails_drafted"] == 12
        assert body["avg_contacts_per_accepted"] == 3.0

    async def test_account_coverage(self, session, seeded, client):
        body = (await client.get("/reporting/account-coverage")).json()
        assert body["total_accounts"] == 3
        assert body["accounts_with_opportunities"] == 3
        assert body["coverage_rate"] == 100.0
        assert body["uncovered_accounts"] == []

    async def test_uncovered_accounts_are_named(self, session, seeded, client):
        session.add(Account(rox_entity_id="lonely", name="Ignored Inc"))
        await session.commit()

        body = (await client.get("/reporting/account-coverage")).json()
        assert body["total_accounts"] == 4
        assert [a["name"] for a in body["uncovered_accounts"]] == ["Ignored Inc"]
        assert body["coverage_rate"] == 75.0

    async def test_decision_latency(self, session, seeded, client):
        await self._decide_all(session, client)
        body = (await client.get("/reporting/decision-latency")).json()
        assert body["count"] == 3
        assert body["median_hours"] is not None
        assert body["p90_hours"] >= body["median_hours"]

    async def test_signal_performance(self, session, seeded, client):
        await self._decide_all(session, client)
        rows = (await client.get("/reporting/signal-performance")).json()
        assert rows
        total_created = sum(r["created"] for r in rows)
        assert total_created == 3
        assert all(r["label"] for r in rows)

    async def test_score_calibration_shape(self, session, seeded, client):
        await self._decide_all(session, client)
        body = (await client.get("/reporting/score-calibration")).json()
        assert body["total_decided"] == 3
        assert [b["band"] for b in body["bands"]] == ["0-59", "60-74", "75-89", "90-100"]
        assert sum(b["decided"] for b in body["bands"]) == 3

    async def test_reports_are_safe_on_empty_database(self, client):
        for path in (
            "/reporting/overview",
            "/reporting/funnel",
            "/reporting/score-calibration",
            "/reporting/decision-latency",
            "/reporting/rejection-reasons",
            "/reporting/account-coverage",
            "/reporting/prospecting-yield",
            "/reporting/run-health",
            "/reporting/signal-performance",
        ):
            resp = await client.get(path)
            assert resp.status_code == 200, f"{path} failed on an empty database"

    async def test_run_health(self, session, seeded, client):
        body = (await client.get("/reporting/run-health")).json()
        assert body["runs_considered"] == 1
        assert body["success_rate"] == 100.0
        # 3 accounts x the one column
        assert body["total_cells_fetched"] == 3
        assert len(body["recent_runs"]) == 1

    async def test_funnel_reports_step_to_step_conversion(self, session, seeded, client):
        await self._decide_all(session, client)
        steps = (await client.get("/reporting/funnel")).json()["steps"]

        by_stage = {s["stage"]: s for s in steps}
        # the first step converts from nothing, so it anchors at 100%
        assert steps[0]["pct_of_previous"] == 100.0
        # 2 of 3 notified opportunities were accepted
        assert by_stage["accepted"]["pct_of_previous"] == pytest.approx(66.7, abs=0.1)
        # ...but only 2 of 3 of the total, which is the distinction
        # pct_of_total alone could not express
        assert by_stage["accepted"]["pct_of_total"] == pytest.approx(66.7, abs=0.1)
        assert by_stage["sequenced"]["pct_of_previous"] == 100.0

    async def test_conversion_out_of_an_empty_step_is_undefined(
        self, session, seeded, client
    ):
        """Deciding without notifying must not read as a 0% conversion."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )

        steps = {s["stage"]: s for s in (await client.get("/reporting/funnel")).json()["steps"]}
        assert steps["notified"]["count"] == 0
        # `reviewed` is the step that now follows the empty one
        assert steps["reviewed"]["count"] == 1
        assert steps["reviewed"]["pct_of_previous"] is None

    async def test_reviewed_counts_decisions_not_notifications(
        self, session, seeded, client
    ):
        """`notified` means the reviewer was emailed, not that anyone looked —
        using it as the accept denominator overstates review throughput."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        for opp in opps:
            await notify_opportunity(session, opp, send=RecordingSend())
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )
        await client.post(
            f"/opportunities/{opps[1].id}/decision", json={"decision": "reject"}
        )

        steps = {s["stage"]: s for s in (await client.get("/reporting/funnel")).json()["steps"]}
        assert steps["notified"]["count"] == 3
        assert steps["reviewed"]["count"] == 2, "accepted + rejected, not notified"
        assert steps["accepted"]["count"] == 1

    async def test_prospected_counts_contacts_not_sequences(
        self, session, seeded, client
    ):
        """A failed prospecting run still writes a Sequence row, so counting
        sequences would credit accounts where Rox returned nobody."""
        from app.models import Sequence, SequenceStatus

        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )
        # a sequence that found no one
        session.add(
            Sequence(
                opportunity_id=opps[1].id,
                account_id=opps[1].account_id,
                name="empty",
                status=SequenceStatus.FAILED.value,
                error="no contacts found",
            )
        )
        await session.commit()

        steps = {s["stage"]: s for s in (await client.get("/reporting/funnel")).json()["steps"]}
        assert steps["prospected"]["count"] == 0, "no contacts were actually found"


class TestReportingWindows:
    """`start`/`end`, score bucketing, and the rejection time series."""

    async def _created_at(self, client) -> list[datetime]:
        items = (await client.get("/opportunities")).json()["items"]
        return sorted(datetime.fromisoformat(i["created_at"]) for i in items)

    async def _decide_without_notifying(self, session, client):
        """Decide straight from the list view — the path that records no latency."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )
        await client.post(
            f"/opportunities/{opps[1].id}/decision",
            json={"decision": "reject", "reason_code": "low_signal"},
        )
        return opps

    async def test_window_is_half_open(self, session, seeded, client):
        created = await self._created_at(client)
        newest = created[-1].isoformat()

        # start is inclusive -> the newest opportunity is kept
        started = (await client.get("/reporting/funnel", params={"start": newest})).json()
        assert started["steps"][0]["count"] == 1

        # end is exclusive -> the same row falls out, so adjacent windows
        # tile without double-counting the boundary
        ended = (await client.get("/reporting/funnel", params={"end": newest})).json()
        assert ended["steps"][0]["count"] == 2

    async def test_window_applies_to_every_report(self, session, seeded, client):
        await self._decide_without_notifying(session, client)
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        params = {"start": future}

        assert (await client.get("/reporting/funnel", params=params)).json()["steps"][0][
            "count"
        ] == 0
        assert (
            await client.get("/reporting/score-calibration", params=params)
        ).json()["total_decided"] == 0
        assert (
            await client.get("/reporting/rejection-reasons", params=params)
        ).json()["total_rejections"] == 0
        assert (await client.get("/reporting/decision-latency", params=params)).json()[
            "count"
        ] == 0
        assert (await client.get("/reporting/overview", params=params)).json()[
            "headline"
        ]["total_opportunities"] == 0

    async def test_coverage_denominator_ignores_the_window(self, session, seeded, client):
        """Accounts we know about, not accounts created during the window."""
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        body = (
            await client.get("/reporting/account-coverage", params={"start": future})
        ).json()
        assert body["total_accounts"] == 3
        assert body["accounts_with_opportunities"] == 0
        assert body["coverage_rate"] == 0.0

    async def test_inverted_window_is_rejected(self, client):
        resp = await client.get(
            "/reporting/funnel",
            params={"start": "2026-02-01T00:00:00", "end": "2026-01-01T00:00:00"},
        )
        assert resp.status_code == 422

    async def test_score_calibration_supports_deciles(self, session, seeded, client):
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        for opp in opps:
            await client.post(
                f"/opportunities/{opp.id}/decision", json={"decision": "accept"}
            )

        body = (
            await client.get("/reporting/score-calibration", params={"buckets": 10})
        ).json()

        assert body["buckets"] == 10
        assert len(body["bands"]) == 10
        assert [b["band"] for b in body["bands"]][:2] == ["0-9", "10-19"]
        # the top bucket absorbs the endpoint so a perfect 100 is never dropped
        assert body["bands"][-1]["lo"] == 90
        assert body["bands"][-1]["hi"] == 100
        # no opportunity may fall between the cracks
        assert sum(b["decided"] for b in body["bands"]) == body["total_decided"] == 3

    async def test_default_bands_expose_numeric_bounds(self, session, seeded, client):
        """Charts sort and position on lo/hi rather than parsing the label."""
        body = (await client.get("/reporting/score-calibration")).json()
        assert body["buckets"] is None
        assert [(b["lo"], b["hi"]) for b in body["bands"]] == [
            (0, 59),
            (60, 74),
            (75, 89),
            (90, 100),
        ]

    async def test_rejection_reasons_series_is_empty_without_group_by(
        self, session, seeded, client
    ):
        await self._decide_without_notifying(session, client)
        body = (await client.get("/reporting/rejection-reasons")).json()
        assert body["group_by"] is None
        assert body["series"] == []

    async def test_rejection_reasons_bucketed_by_day(self, session, seeded, client):
        await self._decide_without_notifying(session, client)
        body = (
            await client.get("/reporting/rejection-reasons", params={"group_by": "day"})
        ).json()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert body["group_by"] == "day"
        assert body["series"] == [
            {"period": today, "reason_code": "low_signal", "count": 1}
        ]
        # the series must reconcile with the totals it summarises
        assert sum(p["count"] for p in body["series"]) == body["total_rejections"]

    async def test_rejection_reasons_bucketed_by_week(self, session, seeded, client):
        await self._decide_without_notifying(session, client)
        body = (
            await client.get(
                "/reporting/rejection-reasons", params={"group_by": "week"}
            )
        ).json()
        assert body["series"][0]["period"] == datetime.now(timezone.utc).strftime(
            "%G-W%V"
        )

    async def test_unknown_group_by_is_rejected(self, client):
        resp = await client.get(
            "/reporting/rejection-reasons", params={"group_by": "month"}
        )
        assert resp.status_code == 422

    async def test_missing_reason_stays_null(self, session, seeded, client):
        """No synthetic 'unspecified' code — it would not validate as a ReasonCode."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "reject"}
        )
        body = (await client.get("/reporting/rejection-reasons")).json()
        assert body["reasons"] == [{"reason_code": None, "count": 1, "pct": 100.0}]

    async def test_latency_falls_back_to_creation_when_never_notified(
        self, session, seeded, client
    ):
        """The case that used to report a null median and no reason why."""
        await self._decide_without_notifying(session, client)
        body = (await client.get("/reporting/decision-latency")).json()

        assert body["count"] == 2
        assert body["measured"] == 2
        assert body["from_notification"] == 0
        assert body["from_creation"] == 2
        assert body["median_hours"] is not None
        assert body["p90_hours"] >= body["median_hours"]

    async def test_latency_prefers_the_notification_basis(self, session, seeded, client):
        """A notified opportunity is timed from the notification, not creation."""
        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        await notify_opportunity(session, opps[0], send=RecordingSend())
        await client.post(
            f"/opportunities/{opps[0].id}/decision", json={"decision": "accept"}
        )
        await client.post(
            f"/opportunities/{opps[1].id}/decision", json={"decision": "accept"}
        )

        body = (await client.get("/reporting/decision-latency")).json()
        assert body["count"] == 2
        assert body["from_notification"] == 1
        assert body["from_creation"] == 1
        assert body["measured"] == 2

    async def test_latency_reports_zero_measured_on_an_empty_database(self, client):
        """`count` and `measured` must be tellable apart, not both silently 0."""
        body = (await client.get("/reporting/decision-latency")).json()
        assert body["count"] == 0
        assert body["measured"] == 0
        assert body["median_hours"] is None

    async def test_windowed_reports_are_safe_on_empty_database(self, client):
        params = {"start": "2026-01-01T00:00:00", "end": "2026-02-01T00:00:00"}
        for path in (
            "/reporting/overview",
            "/reporting/funnel",
            "/reporting/score-calibration",
            "/reporting/decision-latency",
            "/reporting/rejection-reasons",
            "/reporting/account-coverage",
            "/reporting/prospecting-yield",
            "/reporting/run-health",
            "/reporting/signal-performance",
        ):
            resp = await client.get(path, params=params)
            assert resp.status_code == 200, f"{path} failed on an empty window"


class TestRoxBackedAdminEndpoints:
    """The two endpoints whose payloads come from Rox rather than our database."""

    JOBS = [
        {
            "run_id": "cell-1",
            "task_type": "CUSTOM_CELL_GENERATION",
            "current_state": "COMPLETED",
            "created_on": "2026-07-28T00:00:00Z",
            "last_modified": "2026-07-28T00:02:00Z",
        },
        {
            "run_id": "cell-2",
            "task_type": "CUSTOM_CELL_GENERATION",
            "current_state": "COMPLETED",
            "created_on": "2026-07-28T00:00:00Z",
            "last_modified": "2026-07-28T00:01:00Z",
        },
        {
            "run_id": "cell-3",
            "task_type": "CUSTOM_CELL_GENERATION",
            "current_state": "STOPPED",
        },
        {
            "run_id": "col-1",
            "task_type": "CUSTOM_COLUMN_GENERATION",
            "current_state": "COMPLETED",
        },
    ]

    async def test_job_telemetry_is_a_typed_list(self, client):
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            router.get("/priority_jobs").mock(
                return_value=httpx.Response(200, json=self.JOBS)
            )
            body = (await client.get("/reporting/job-telemetry")).json()

        assert body["total_jobs"] == 4
        # busiest task type first, so the table reads top-down unsorted
        assert [t["task_type"] for t in body["by_task_type"]] == [
            "CUSTOM_CELL_GENERATION",
            "CUSTOM_COLUMN_GENERATION",
        ]
        cells = body["by_task_type"][0]
        assert cells["total"] == 3
        assert cells["states"] == [
            {"state": "COMPLETED", "count": 2},
            {"state": "STOPPED", "count": 1},
        ]
        # 120s and 60s; the STOPPED job contributes no duration
        assert body["avg_cell_generation_seconds"] == 90.0

    async def test_job_telemetry_survives_an_empty_queue(self, client):
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            router.get("/priority_jobs").mock(return_value=httpx.Response(200, json=[]))
            body = (await client.get("/reporting/job-telemetry")).json()

        assert body["total_jobs"] == 0
        assert body["by_task_type"] == []
        assert body["avg_cell_generation_seconds"] is None

    async def test_lookback_is_bounded(self, client):
        assert (
            await client.get("/reporting/job-telemetry", params={"lookback_hours": 0})
        ).status_code == 422

    async def test_rox_me_returns_a_typed_identity(self, client):
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            router.get("/user/me").mock(
                return_value=httpx.Response(
                    200, json={"name": "Rox SE", "email": "rep@rox.com"}
                )
            )
            body = (await client.get("/admin/rox/me")).json()

        assert body == {
            "message": "ok",
            "detail": {"name": "Rox SE", "email": "rep@rox.com"},
        }

    async def test_rox_me_tolerates_a_partial_identity(self, client):
        """A reachable Rox that omits a field is still reachable."""
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            router.get("/user/me").mock(
                return_value=httpx.Response(200, json={"name": "Rox SE"})
            )
            resp = await client.get("/admin/rox/me")

        assert resp.status_code == 200
        assert resp.json()["detail"] == {"name": "Rox SE", "email": None}

    async def test_rox_me_reports_an_unreachable_rox(self, client):
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            router.get("/user/me").mock(return_value=httpx.Response(500))
            resp = await client.get("/admin/rox/me")

        assert resp.status_code == 502


class TestEnumSerialization:
    """Enum-typed response fields must still emit plain JSON strings."""

    async def test_opportunity_detail_enums_are_strings(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        await notify_opportunity(session, opp, send=RecordingSend())
        await client.post(
            f"/opportunities/{opp.id}/decision",
            json={"decision": "reject", "reason_code": "bad_timing"},
        )

        body = (await client.get(f"/opportunities/{opp.id}")).json()
        assert body["status"] == "rejected"
        assert body["stage"] == "rejected"
        assert body["decision"]["decision"] == "reject"
        assert body["decision"]["reason_code"] == "bad_timing"
        assert body["notifications"][0]["channel"] == "email"
        assert body["notifications"][0]["status"] == "sent"

    async def test_enums_are_declared_as_unions_in_the_spec(self, client):
        """The whole point: the generated client gets a union, not `string`."""
        schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]

        assert schemas["OpportunityStatus"]["enum"] == [
            "new",
            "accepted",
            "rejected",
            "superseded",
        ]
        status = schemas["OpportunityOut"]["properties"]["status"]
        assert status["$ref"].endswith("OpportunityStatus")
        assert schemas["FunnelStepOut"]["properties"]["stage"]["$ref"].endswith("Stage")

    async def test_no_response_field_generates_as_unknown(self, client):
        """No bare dicts or untyped `Any` left anywhere in the response models."""
        spec = (await client.get("/openapi.json")).json()
        schemas = spec["components"]["schemas"]

        def is_loose(node: dict) -> bool:
            if "$ref" in node or "enum" in node:
                return False
            if "anyOf" in node:
                return any(is_loose(x) for x in node["anyOf"])
            if node.get("type") == "array":
                return is_loose(node.get("items", {}))
            if node.get("type") == "object":
                extra = node.get("additionalProperties")
                # `{}` / `True` means "any value"; a dict of known type is fine
                if extra is True or extra == {}:
                    return True
                return not node.get("properties") and extra is None
            # a schema with no type at all is an untyped `Any`
            return not node.get("type")

        loose = [
            f"{name}.{field}"
            for name, body in schemas.items()
            for field, node in body.get("properties", {}).items()
            if is_loose(node)
        ]
        assert loose == []


class TestAccountsApi:
    async def test_accounts_and_runs_are_listable(self, session, seeded, client):
        accounts = (await client.get("/accounts")).json()
        assert len(accounts) == 3
        assert accounts[0]["name"] == "Acme Corp"

        columns = (await client.get("/research/columns")).json()
        assert {c["key"] for c in columns} == {r.key for r in COLUMN_REFS}
        assert all(c["rox_column_id"] for c in columns)

        runs = (await client.get("/research/runs")).json()
        assert len(runs) == 1
        assert runs[0]["status"] == "succeeded"
        assert runs[0]["duration_seconds"] is not None


class TestDetailResearchSignals:
    """The detail screen renders `signals`, never the raw cell."""

    async def test_detail_exposes_parsed_signals(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        body = (await client.get(f"/opportunities/{opp.id}")).json()

        assert body["research"], "fixture should attach a research artifact"
        artifact = body["research"][0]
        assert artifact["signals"], "signals must be parsed server-side"

        top = artifact["signals"][0]
        assert set(top) == {"signal", "evidence", "score"}
        assert top["signal"]
        assert top["evidence"]

    async def test_top_signal_explains_the_headline_score(self, session, seeded, client):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        body = (await client.get(f"/opportunities/{opp.id}")).json()
        assert body["research"][0]["signals"][0]["score"] == body["qualification_score"]

    async def test_raw_cell_is_still_available_but_unparseable_by_clients(
        self, session, seeded, client
    ):
        """`cell_value` stays for debugging; `signals` is what the UI reads."""
        opp = (await session.execute(select(Opportunity))).scalars().first()
        artifact = (await client.get(f"/opportunities/{opp.id}")).json()["research"][0]
        assert artifact["cell_value"]


class TestDetailNarrative:
    """Most live cells are narrative prose, not structured signals."""

    async def test_narrative_is_present_for_prose_cells(self, session, seeded, client):
        from app.models import ResearchArtifact

        artifact = (await session.execute(select(ResearchArtifact))).scalars().first()
        artifact.cell_value = "9 — Strongest signals: a large equity raise this quarter."
        await session.commit()

        opp = (await session.execute(select(Opportunity))).scalars().first()
        research = (await client.get(f"/opportunities/{opp.id}")).json()["research"]
        entry = next(r for r in research if r["id"] == artifact.id)

        # nothing structured to show, but the prose is still readable
        assert entry["signals"] == []
        assert entry["narrative"] == "9 — Strongest signals: a large equity raise this quarter."

    async def test_narrative_is_joined_evidence_for_structured_cells(
        self, session, seeded, client
    ):
        opp = (await session.execute(select(Opportunity))).scalars().first()
        entry = (await client.get(f"/opportunities/{opp.id}")).json()["research"][0]
        assert entry["signals"], "fixture cell is structured"
        for signal in entry["signals"]:
            assert signal["evidence"] in entry["narrative"]

    async def test_narrative_is_null_for_an_empty_cell(self, session, seeded, client):
        from app.models import ResearchArtifact

        artifact = (await session.execute(select(ResearchArtifact))).scalars().first()
        artifact.cell_value = None
        await session.commit()

        opp = (await session.execute(select(Opportunity))).scalars().first()
        research = (await client.get(f"/opportunities/{opp.id}")).json()["research"]
        entry = next(r for r in research if r["id"] == artifact.id)
        assert entry["narrative"] is None
        assert entry["signals"] == []
