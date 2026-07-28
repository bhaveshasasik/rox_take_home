import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Must be set before app.config is imported, since Settings reads the env at
# construction and get_settings() is lru_cached. Poll timeouts in particular:
# the production 120s ceiling would make the timeout tests take minutes.
os.environ.update(
    {
        "ROX_BASE_URL": "https://rox.test",
        "ROX_API_TOKEN": "test-token",
        # job polling: real intervals are 5s/10s, which would make the suite
        # take minutes
        "JOB_DISCOVERY_ATTEMPTS": "2",
        "JOB_DISCOVERY_INTERVAL_SECONDS": "0",
        "JOB_POLL_INTERVAL_SECONDS": "0",
        "JOB_POLL_TIMEOUT_SECONDS": "5",
        "ROX_ORG_ID": "b2a7ec35-8d8e-4e35-9355-8c29a5220a3f",
        "SMTP_HOST": "",
        "APP_BASE_URL": "http://localhost:3000",
    }
)

from app.db import Base  # noqa: E402
from app.services.outreach import DraftedEmail, DraftedSequence  # noqa: E402

FAKE_DRAFTED_SEQUENCE = DraftedSequence(
    initial=DraftedEmail(subject="Test account + Rox", body="Test outreach body."),
    follow_up=DraftedEmail(subject="Re: Test account + Rox", body="Test follow-up body."),
)

FAKE_NOTIFICATION_SUMMARY = "This is a mocked one-sentence notification summary."


@pytest.fixture(autouse=True)
def _no_real_llm_calls():
    """Never let the suite hit the real Anthropic API by default.

    Three call sites are unconditional-by-construction from a test's point
    of view: `prospecting.build_sequence` calls `draft_emails` for every
    contact on every `run_prospecting()` call; `opportunities.create_opportunities_for_run`
    calls `expand_rationale` for any artifact scoring above
    `llm_expansion_score_threshold`, and also calls `notify_batch` once after
    every run unless a test asks otherwise. A test that forgets to mock any
    of these would otherwise make a real, billed API call. Individual tests
    can still override these with their own `patch(...)` to test failure
    paths, and tests that want to exercise notification directly call
    `notifications.notify_opportunity` / `notifications.notify_batch`
    themselves — a different reference than the one patched here, so it's
    unaffected by this fixture.
    """
    with (
        patch(
            "app.services.prospecting.draft_emails",
            AsyncMock(return_value=FAKE_DRAFTED_SEQUENCE),
        ),
        patch("app.services.opportunities.expand_rationale", AsyncMock(return_value=None)),
        patch("app.services.opportunities.notify_batch", AsyncMock(return_value=None)),
        patch(
            "app.services.notifications.write_notification_summary",
            AsyncMock(return_value=FAKE_NOTIFICATION_SUMMARY),
        ),
    ):
        yield


@pytest_asyncio.fixture
async def session():
    """Fresh in-memory SQLite per test."""
    from app import models  # noqa: F401  — register mappers

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s

    await engine.dispose()
