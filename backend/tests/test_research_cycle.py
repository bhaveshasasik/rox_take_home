"""Research cycle against a mocked Rox API.

The mock mirrors the real endpoint set: resolve columns from
/account_research/account_research_section, trigger via refresh_by_tab, observe
work in /priority_jobs, bulk read via /agents/customers_paginated.
"""

import httpx
import respx
from sqlalchemy import select

from app.models import (
    Account,
    Opportunity,
    OpportunityResearchLink,
    OpportunityStatus,
    ResearchArtifact,
    ResearchColumn,
    ResearchRun,
    RunStatus,
    Stage,
    utcnow,
)
from app.rox.client import RoxClient
from app.services.opportunities import should_skip_account
from app.services.research import resolve_columns, run_research_cycle
from app.services.research_columns import COLUMN_REFS, ColumnRef

BASE = "https://rox.test"
ORG = "b2a7ec35-8d8e-4e35-9355-8c29a5220a3f"

HIERARCHY = [
    {
        "customer_name": "Acme Corp",
        "customer_id": "ent-1",
        "domain": "acme.com",
        "hierarchy_parent_id": None,
        "children": [
            {
                "customer_name": "Acme Europe",
                "customer_id": "ent-2",
                "domain": "acme.eu",
                "children": [],
            }
        ],
    },
    {
        "customer_name": "Globex",
        "customer_id": "ent-3",
        "domain": "globex.com",
        "hierarchy_parent_id": None,
        "children": [],
    },
]

ENTITIES = ["ent-1", "ent-2", "ent-3"]

#: column name -> column_id, mirroring the real org. "Extra Column" is unused
#: by the real registry (a single required column) and exists only so
#: `test_unreadable_column_is_skipped` has a second, non-required column to
#: probe the readability-skip path with.
COLUMN_IDS = {
    "Opportunity-Signal Research": "col-opp",
    "Extra Column": "col-extra",
}

# Real captured format: a JSON array of {signal, evidence, score} objects,
# score 0-10. `default_opp` stands in for what every account gets unless
# overridden per-entity.
STRONG_OPP = (
    '[{"signal":"Trigger Events","evidence":"Confirmed leadership reorganization '
    '(SEC 8-K) and major partner signals indicate near-term expansion '
    'opportunity.","score":8}]'
)
WEAK_OPP = (
    '[{"signal":"Growth / Expansion","evidence":"No evidence of new funding or '
    'investor-led financing in the past 180 days; capital return rather than '
    'expansion.","score":2}]'
)


def mock_rox(
    *,
    opp_by_entity: dict | None = None,
    default_opp: str = STRONG_OPP,
    enqueue_jobs: bool = True,
    unreadable: set[str] | None = None,
):
    """Install the full endpoint set. `unreadable` column_ids raise 403."""
    router = respx.mock(base_url=BASE, assert_all_called=False)
    unreadable = unreadable or set()

    router.get("/user/me").mock(
        return_value=httpx.Response(200, json={"name": "Rox SE", "email": "rep@rox.com"})
    )
    router.get("/hierarchy/customers").mock(
        return_value=httpx.Response(200, json=HIERARCHY)
    )
    router.get("/priorities").mock(
        return_value=httpx.Response(
            200,
            # underscore form, as the live API returns it
            json=[{"label": "HIGH", "rox_org_id": ORG.replace("-", "_")}],
        )
    )
    router.get("/account_research/account_research_section").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": f"sec-{cid}", "name": name, "column_id": cid}
                for name, cid in COLUMN_IDS.items()
            ],
        )
    )

    jobs: list[dict] = []
    counter = {"n": 0}

    def refresh(request):
        column_id = request.url.path.split("/")[-3]
        if enqueue_jobs:
            counter["n"] += 1
            parent = f"parent-{counter['n']}"
            jobs.append(
                {
                    "run_id": parent,
                    "task_type": "CUSTOM_COLUMN_GENERATION",
                    "artifact_id": column_id,
                    "current_state": "COMPLETED",
                    "parent_task_run_id": None,
                }
            )
            for entity in ENTITIES:
                counter["n"] += 1
                jobs.append(
                    {
                        "run_id": f"cell-{counter['n']}",
                        "task_type": "CUSTOM_CELL_GENERATION",
                        "artifact_id": entity,
                        "current_state": "COMPLETED",
                        "parent_task_run_id": parent,
                    }
                )
        return httpx.Response(201, json={"message": "Successfully triggered refresh"})

    router.post(url__regex=r".*/refresh_by_tab/.*").mock(side_effect=refresh)
    router.get("/priority_jobs").mock(side_effect=lambda r: httpx.Response(200, json=jobs))

    def paginated(request):
        column_id = request.url.path.rstrip("/").split("/")[-1]
        if column_id in unreadable:
            return httpx.Response(403, json={"messages": ["not available for the user"]})

        name = next((n for n, c in COLUMN_IDS.items() if c == column_id), "Unknown")
        rows = []
        for entity in ENTITIES:
            value = (opp_by_entity or {}).get(entity, default_opp) if column_id == "col-opp" else None
            rows.append(
                {
                    "customer_id": entity,
                    "domain": f"{entity}.com",
                    name: value,
                    "section_id": f"sec-{column_id}",
                    "column_name": name,
                    "value_structured": {"string_value": value} if value else None,
                }
            )
        # order is non-deterministic on the real API
        return httpx.Response(200, json=list(reversed(rows)))

    router.get(url__regex=r".*/agents/customers_paginated/.*").mock(side_effect=paginated)

    def per_entity_cell(request):
        parts = request.url.path.rstrip("/").split("/")
        column_id, entity = parts[-3], parts[-1]
        if column_id in unreadable:
            return httpx.Response(403, json={"messages": ["not available for the user"]})

        value = (opp_by_entity or {}).get(entity, default_opp) if column_id == "col-opp" else None
        return httpx.Response(
            200,
            json={
                "column_id": column_id,
                "entity_id": entity,
                "rox_company_id": entity,
                "cell_value": value or "",
                "output": {"type": "text", "text": value or "", "sources": []},
                "updated_at": "2026-01-01T00:00:00Z",
            },
        )

    router.get(url__regex=r".*/research/clever_column/[^/]+/cell/.*").mock(
        side_effect=per_entity_cell
    )
    return router


def client() -> RoxClient:
    return RoxClient(base_url=BASE, token="t")


class TestResearchCycle:
    async def test_full_cycle(self, session):
        with mock_rox():
            async with client() as rox:
                run = await run_research_cycle(session, rox, trigger="manual")

        assert run.status == RunStatus.SUCCEEDED.value
        assert run.error is None
        assert run.accounts_scanned == 3

        accounts = (await session.execute(select(Account))).scalars().all()
        assert {a.name for a in accounts} == {"Acme Corp", "Acme Europe", "Globex"}
        europe = next(a for a in accounts if a.name == "Acme Europe")
        assert europe.parent_rox_entity_id == "ent-1"

        columns = (await session.execute(select(ResearchColumn))).scalars().all()
        assert {c.key for c in columns} == {r.key for r in COLUMN_REFS}
        assert all(c.rox_column_id for c in columns)
        assert all(c.last_refreshed_at for c in columns)

        # every column refreshed, and every cell job waited on
        assert run.columns_refreshed == len(COLUMN_REFS)
        assert run.jobs_enqueued > 0
        assert run.jobs_completed == run.jobs_enqueued - len(COLUMN_REFS)

        artifacts = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert len(artifacts) == 3 * len(COLUMN_REFS)

        opps = (await session.execute(select(Opportunity))).scalars().all()
        assert len(opps) == 3
        for opp in opps:
            assert opp.status == OpportunityStatus.NEW.value
            assert opp.stage == Stage.OPPORTUNITY_CREATED.value

    async def test_zero_to_ten_scale_is_normalized(self, session):
        """A raw score of 8 must qualify as 80, not 8.

        Regression guard: parsed as 8 it would fall under the threshold and
        every opportunity would be silently dropped.
        """
        with mock_rox():
            async with client() as rox:
                await run_research_cycle(session, rox)

        opps = (await session.execute(select(Opportunity))).scalars().all()
        assert opps
        for opp in opps:
            assert opp.qualification_score > 50, "0-10 value was not scaled to 0-100"
            assert opp.qualification_score <= 100

    async def test_bulk_row_order_does_not_misassign(self, session):
        """The mock returns rows reversed; artifacts must still match accounts."""
        with mock_rox(opp_by_entity={"ent-1": STRONG_OPP}, default_opp=WEAK_OPP):
            async with client() as rox:
                await run_research_cycle(session, rox)

        opps = (await session.execute(select(Opportunity))).scalars().all()
        assert len(opps) == 1
        account = (
            await session.execute(select(Account).where(Account.id == opps[0].account_id))
        ).scalar_one()
        assert account.name == "Acme Corp", "row order leaked into account mapping"

    async def test_supporting_research_attached(self, session):
        with mock_rox():
            async with client() as rox:
                await run_research_cycle(session, rox)

        opp = (await session.execute(select(Opportunity))).scalars().first()
        links = (
            (
                await session.execute(
                    select(OpportunityResearchLink).where(
                        OpportunityResearchLink.opportunity_id == opp.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 1  # the one column that produced the score

    async def test_weak_signal_creates_nothing(self, session):
        with mock_rox(default_opp=WEAK_OPP):
            async with client() as rox:
                run = await run_research_cycle(session, rox)

        assert run.opportunities_created == 0
        assert (await session.execute(select(ResearchArtifact))).scalars().all()

    async def test_refresh_with_no_jobs_degrades_not_fails(self, session):
        """The API-created-column failure mode: refresh accepted, nothing queued.

        The run must still read existing values rather than failing.
        """
        with mock_rox(enqueue_jobs=False):
            async with client() as rox:
                run = await run_research_cycle(session, rox)

        assert run.status == RunStatus.PARTIAL.value
        assert run.columns_refreshed == 0
        assert run.cells_timed_out == len(COLUMN_REFS)
        assert run.cells_fetched > 0, "should still read existing cell values"
        assert run.opportunities_created == 3

    async def test_unreadable_column_is_skipped(self, session):
        """~30% of columns 403; a non-required one must not kill resolution."""
        refs = [
            COLUMN_REFS[0],
            ColumnRef(key="extra", rox_name="Extra Column", required=False),
        ]
        with mock_rox(unreadable={"col-extra"}):
            async with client() as rox:
                resolved = await resolve_columns(session, rox, refs=refs)

        keys = {c.key for c, _ in resolved}
        assert "extra" not in keys
        assert "opportunity_signal" in keys

    async def test_stale_column_is_deactivated(self, session):
        """A column dropped from the registry stops reading as active."""
        stale = ResearchColumn(key="stale_column", name="Old Column", active=True)
        session.add(stale)
        await session.commit()

        with mock_rox():
            async with client() as rox:
                await resolve_columns(session, rox)

        await session.refresh(stale)
        assert stale.active is False

    async def test_required_column_missing_fails_run(self, session):
        router = mock_rox()
        router.get("/account_research/account_research_section").mock(
            return_value=httpx.Response(200, json=[])
        )
        with router:
            async with client() as rox:
                run = await run_research_cycle(session, rox)

        assert run.status == RunStatus.FAILED.value
        assert "Opportunity-Signal Research" in (run.error or "")

    async def test_refresh_can_be_disabled(self, session):
        router = mock_rox()
        with router:
            async with client() as rox:
                run = await run_research_cycle(session, rox, refresh=False)

        assert run.jobs_enqueued == 0
        assert run.cells_fetched > 0, "reads current values without refreshing"


class TestDedupe:
    async def _seed(self, session, **overrides):
        account = Account(rox_entity_id="e1", name="Acme")
        session.add(account)
        await session.flush()
        run = ResearchRun()
        session.add(run)
        await session.flush()
        opp = Opportunity(
            account_id=account.id,
            run_id=run.id,
            title="t",
            rationale="r",
            qualification_score=80,
            signal_type="funding_financial",
            **overrides,
        )
        session.add(opp)
        await session.commit()
        return account, opp

    async def test_open_opportunity_blocks(self, session):
        account, _ = await self._seed(session, status=OpportunityStatus.NEW.value)
        reason = await should_skip_account(session, account.id)
        assert reason and "pending review" in reason

    async def test_dedupe_is_per_account_not_per_signal(self, session):
        """signal_type is derived from the cell's own label and can drift between
        runs, so a different label must not let a duplicate through."""
        account, _ = await self._seed(session, status=OpportunityStatus.NEW.value)
        assert await should_skip_account(session, account.id, "leadership_change")

    async def test_recent_decision_in_cooldown(self, session):
        from datetime import timedelta

        account, _ = await self._seed(
            session,
            status=OpportunityStatus.REJECTED.value,
            decided_at=(utcnow() - timedelta(days=1)).replace(tzinfo=None),
        )
        reason = await should_skip_account(session, account.id)
        assert reason and "cooldown" in reason

    async def test_old_decision_allows_resurfacing(self, session):
        from datetime import timedelta

        account, _ = await self._seed(
            session,
            status=OpportunityStatus.REJECTED.value,
            decided_at=(utcnow() - timedelta(days=30)).replace(tzinfo=None),
        )
        assert await should_skip_account(session, account.id) is None

    async def test_repeat_runs_do_not_duplicate(self, session):
        with mock_rox():
            async with client() as rox:
                first = await run_research_cycle(session, rox)
                second = await run_research_cycle(session, rox)

        assert first.opportunities_created == 3
        assert second.opportunities_created == 0
        assert len((await session.execute(select(Opportunity))).scalars().all()) == 3


class TestFullTextIsPreferred:
    """`cell_value` is Rox's ~300-char capped copy of `output.text`.

    Reading it costs every signal past the first: an account with 2-16 signals
    arrives truncated mid-sentence, and the JSON variant is cut mid-object so
    not even one survives.
    """

    async def test_artifact_stores_output_text_not_the_capped_cell_value(self, session):
        full = (
            "Conclusion: two distinct signals.\n\nEvidence\n\n"
            "- Growth / Expansion: revenue up 28% YoY.\n  - Confidence: 8\n\n"
            "- Trigger Events: new CFO appointed in June.\n  - Confidence: 6\n"
        )
        capped = full[:300] + "..."

        with mock_rox() as router:
            router.get(url__regex=r".*/research/clever_column/[^/]+/cell/.*").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "cell_value": capped,
                        "output": {"type": "text", "text": full, "sources": []},
                    },
                )
            )
            async with client() as rox:
                await run_research_cycle(session, rox, trigger="manual")

        artifacts = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert artifacts
        stored = artifacts[0].cell_value
        assert stored == full, "must keep the full narrative, not the capped copy"
        assert not stored.endswith("...")

    async def test_falls_back_to_cell_value_when_output_is_absent(self, session):
        """Older cells and other columns may only carry `cell_value`."""
        with mock_rox() as router:
            router.get(url__regex=r".*/research/clever_column/[^/]+/cell/.*").mock(
                return_value=httpx.Response(
                    200, json={"cell_value": "7 — a single terse signal."}
                )
            )
            async with client() as rox:
                await run_research_cycle(session, rox, trigger="manual")

        artifacts = (await session.execute(select(ResearchArtifact))).scalars().all()
        assert artifacts[0].cell_value == "7 — a single terse signal."
