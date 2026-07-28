"""LLM rationale expansion for strong opportunities.

Rox's per-entity cell endpoint returns `output.text` — a full, untruncated
research narrative — alongside the ~300-char-capped `cell_value` we score
from. For opportunities at or above `llm_expansion_score_threshold`, this
module turns that narrative into a headline + rationale worth a reviewer's
time. It never touches the score: qualification stays entirely deterministic
in `opportunities.py`, this is prose only.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """You turn account research into the opening of a sales opportunity brief.

You will be given a research narrative about a company, written by another
analysis system, plus the account name. Write:

- headline: one sentence, under 90 characters, no analyst preamble
  ("Strongest signals are..."), stating the single most compelling reason
  this account is worth pursuing right now.
- rationale: 2-4 sentences a sales rep would read before reaching out. Lead
  with the driving signal, then add supporting context. Plain prose, no
  bullet points, no markdown, no citation brackets.

Only use facts present in the narrative. Do not invent numbers, dates, or
sources."""


class ExpandedRationale(BaseModel):
    headline: str
    rationale: str


NOTIFICATION_SYSTEM_PROMPT = """You write the opening summary of an internal email that \
tells a sales rep a new opportunity was just qualified.

You will be given the account name, the signal category driving this opportunity, and \
the supporting rationale. Write 1-3 sentences of plain prose — no headline, no bullet \
points, no markdown, no citation brackets — explaining why this account is worth acting \
on right now. Name the actual signal category rather than a vague phrase like "activity"
or "engagement". Only use facts present in the rationale; never invent numbers, dates, or
sources."""


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


async def expand_rationale(account_name: str, narrative: str) -> ExpandedRationale | None:
    """Rewrite a research narrative into a headline + rationale. Returns None on any failure."""
    if not narrative.strip():
        return None

    try:
        response = await _get_client().messages.parse(
            model="claude-opus-5",
            max_tokens=1024,
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Account: {account_name}\n\nResearch narrative:\n{narrative}",
                }
            ],
            output_format=ExpandedRationale,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to the terse rationale, never fail the run
        log.warning("rationale expansion failed", account=account_name, error=str(exc)[:300])
        return None

    if response.stop_reason == "refusal":
        log.warning("rationale expansion refused", account=account_name)
        return None

    return response.parsed_output


async def write_notification_summary(
    account_name: str, signal_label: str, rationale: str
) -> str | None:
    """1-3 sentence email body for the new-opportunity notification.

    Takes whatever rationale is already on the opportunity — the full
    LLM-expanded version for strong signals, the terse evidence-bullet
    version otherwise — so this never re-truncates what opportunities.py
    already decided was the fullest text worth keeping.
    """
    if not rationale.strip():
        return None

    try:
        response = await _get_client().messages.create(
            model="claude-opus-5",
            max_tokens=300,
            output_config={"effort": "low"},
            system=NOTIFICATION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Account: {account_name}\n"
                        f"Signal: {signal_label}\n\n"
                        f"Rationale:\n{rationale}"
                    ),
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - degrade to the raw rationale, never fail the send
        log.warning("notification summary failed", account=account_name, error=str(exc)[:300])
        return None

    if response.stop_reason == "refusal":
        log.warning("notification summary refused", account=account_name)
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or None
