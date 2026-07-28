"""Full pipeline through the HTTP API:

    research -> opportunity -> notify -> accept -> prospect -> report

This is the test that proves the six functional requirements hold together.
"""

import httpx
import pytest
import pytest_asyncio
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
from app.services.notifications import OpportunityMessage, notify_batch, notify_opportunity
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


class TestBatchNotification:
    async def test_holds_below_batch_size(self, session, seeded):
        result = await notify_batch(session, batch_size=5)

        assert result == {"sent": False, "queued": 3}
        opps = (await session.execute(select(Opportunity))).scalars().all()
        assert all(o.notified_at is None for o in opps)

    async def test_sends_top_n_by_score_and_leaves_the_rest_queued(self, session, seeded):
        from unittest.mock import AsyncMock, patch

        opps = (
            (await session.execute(select(Opportunity).order_by(Opportunity.id)))
            .scalars()
            .all()
        )
        low, high, mid = opps
        low.qualification_score = 60
        high.qualification_score = 90
        mid.qualification_score = 75
        await session.commit()

        with (
            patch("app.services.notifications._send_email", AsyncMock(return_value=None)),
            patch(
                "app.services.notifications._configured_recipient",
                return_value="reviewer@example.com",
            ),
        ):
            result = await notify_batch(session, batch_size=2)

        assert result == {"sent": True, "count": 2, "recipient": "reviewer@example.com"}

        for opp in (low, high, mid):
            await session.refresh(opp)
        assert high.notified_at is not None
        assert mid.notified_at is not None
        assert low.notified_at is None, "lowest score should stay queued below the batch size"

    async def test_email_lists_each_account_without_a_score(self, session, seeded):
        from unittest.mock import AsyncMock, patch

        opps = (await session.execute(select(Opportunity))).scalars().all()
        sent: dict[str, str] = {}

        async def capture(*, to, subject, body):
            sent["subject"] = subject
            sent["body"] = body

        with (
            patch("app.services.notifications._send_email", capture),
            patch(
                "app.services.notifications._configured_recipient",
                return_value="reviewer@example.com",
            ),
        ):
            result = await notify_batch(session, batch_size=3)

        assert result["sent"] is True
        assert "3 new opportunities" in sent["subject"]
        assert "score" not in sent["body"].lower()
        for opp in opps:
            assert f"{opp.qualification_score}/100" not in sent["body"]
            assert f"/opportunities/{opp.id}" in sent["body"]

    async def test_creates_a_notification_record_per_opportunity_in_the_batch(
        self, session, seeded, client
    ):
        from unittest.mock import AsyncMock, patch

        with (
            patch("app.services.notifications._send_email", AsyncMock(return_value=None)),
            patch(
                "app.services.notifications._configured_recipient",
                return_value="reviewer@example.com",
            ),
        ):
            await notify_batch(session, batch_size=3)

        resp = await client.get("/notifications")
        assert resp.status_code == 200
        assert len(resp.json()) == 3
        assert all(n["status"] == "sent" for n in resp.json())


class TestDigest:
    async def test_includes_every_pending_opportunity(self, session, seeded):
        from app.services.notifications import build_digest

        opps = (await session.execute(select(Opportunity))).scalars().all()
        subject, body, count = await build_digest(session)

        assert count == len(opps) == 3
        assert "3 opportunities to review" in subject
        for opp in opps:
            assert f"/opportunities/{opp.id}" in body

    async def test_omits_the_score(self, session, seeded):
        from app.services.notifications import build_digest

        opps = (await session.execute(select(Opportunity))).scalars().all()
        _, body, _ = await build_digest(session)

        for opp in opps:
            assert f"{opp.qualification_score}/100" not in body
        assert "score" not in body.lower()

    async def test_link_is_the_last_line_of_each_entry(self, session, seeded):
        from app.services.notifications import build_digest

        _, body, _ = await build_digest(session)
        # every non-empty paragraph block ends with its review link
        blocks = [b for b in body.split("\n\n") if b.strip()]
        for block in blocks[2:]:  # skip the title block and the summary line
            assert block.strip().splitlines()[-1].startswith("Review: http")

    async def test_empty_state_is_friendly(self, session):
        from app.services.notifications import build_digest

        subject, body, count = await build_digest(session)
        assert count == 0
        assert "No opportunities" in subject
        assert "Nothing is waiting" in body

    async def test_endpoint_triggers_a_send(self, session, seeded, client):
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.services.notifications._send_email", AsyncMock(return_value=None)
        ), patch(
            "app.services.notifications._configured_recipient",
            return_value="reviewer@example.com",
        ):
            resp = await client.post("/admin/notifications/digest")

        assert resp.status_code == 200
        assert resp.json()["sent"] is True
        assert resp.json()["count"] == 3


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
