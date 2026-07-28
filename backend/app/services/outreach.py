"""LLM-drafted outreach email sequences.

Replaces the old template-based copywriting (headline/lede/truncate/dedupe
string surgery) with a direct generation step: give the model the opportunity
and the contact, let it write the email. This only ever runs after a human
has accepted an opportunity, so the cost is bounded by real interest, not by
research volume.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """You write a two-step cold outreach email sequence for a B2B sales rep.

You'll be given an account, a specific contact at that account, the buying
signal that qualified this account, and the research rationale behind it.
Write:

- initial: the first email. Subject under 60 characters, no clickbait/hype.
  Body: 2-3 short paragraphs, personalized to the contact's title and the
  specific signal, grounded only in the facts given. Plain text, no markdown,
  no bullet points. Sign off as "The Rox Team".
- follow_up: a brief nudge, sent about a week later if there's no reply.
  Subject should reference the first email (e.g. "Re: ..."). Body: short,
  don't repeat the first email's details verbatim, just a light nudge with a
  different angle (e.g. offer to share how similar companies handled this).

Only use facts present in the rationale you're given. Never invent statistics,
dates, or people. Never mention internal system details (scores, column
names, signal type codes) — the contact must never see any of that."""


class DraftedEmail(BaseModel):
    subject: str
    body: str


class DraftedSequence(BaseModel):
    initial: DraftedEmail
    follow_up: DraftedEmail


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


async def draft_emails(
    *,
    account_name: str,
    contact_name: str,
    contact_title: str,
    signal_label: str,
    rationale: str,
) -> DraftedSequence | None:
    """Draft a two-step outreach sequence for one contact. Returns None on any failure."""
    user_content = (
        f"Account: {account_name}\n"
        f"Contact: {contact_name}, {contact_title}\n"
        f"Signal: {signal_label}\n\n"
        f"Research rationale:\n{rationale}"
    )

    try:
        response = await _get_client().messages.parse(
            model="claude-opus-5",
            max_tokens=1536,
            output_config={"effort": "high"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_format=DraftedSequence,
        )
    except Exception as exc:  # noqa: BLE001 - one contact's draft must not kill the sequence
        log.warning("email drafting failed", contact=contact_name, error=str(exc)[:300])
        return None

    if response.stop_reason == "refusal":
        log.warning("email drafting refused", contact=contact_name)
        return None

    return response.parsed_output
