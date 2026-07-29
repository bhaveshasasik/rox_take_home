"""Per-account brief, written from stored signals.

The distinction from `services/rationale.expand_rationale` is the input. That
function re-reads Rox's raw narrative and asks the model to find the story in
it — a second pass over the same prose the extraction already read. This one
takes the typed signals out of the database and asks only for elaboration:
what these findings mean together, and why now.

That makes the brief attributable. Every claim traces to a signal, every
signal to a verbatim evidence span, every span to a whitelisted source.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.config import get_settings
from app.logging_config import get_logger
from app.signals.schema import ExtractedSignal, SourceRef, signal_label

log = get_logger(__name__)

SYSTEM_PROMPT = """You write the opening of a sales opportunity brief.

You will be given an account name and the structured signals another system
extracted from research on it. Each signal carries a category, a confidence
from 0-10, whether it is an absence, and a verbatim evidence span. Write:

- headline: one sentence under 90 characters stating the single most
  compelling reason to pursue this account now. No analyst preamble
  ("Strongest signals are..."), no score, no category jargon.
- rationale: 2-4 sentences, at most 60 words, that a rep reads before reaching
  out. Lead with the driving signal, then the supporting ones.
- why_now: one or two sentences, at most 30 words, on timing specifically —
  what has changed or is about to, and what the window is. If the signals
  genuinely say nothing about timing, say that plainly rather than inventing
  urgency.

Rules:

- Every claim must be traceable to a specific signal below. If a rep asked
  "where did that come from?", you must be able to point at one. Do not
  introduce facts, figures, company details, dates, names, job titles, product
  names or URLs that do not appear in the signals — not even ones you believe
  to be true about this company.
- Prefer stating less over padding to reach a length. The limits are ceilings,
  not targets.
- Use only what the signals state. Never invent numbers, dates, names or URLs.
- Weigh by confidence, but do not quote the numbers back.
- Signals marked as an absence are findings that something is NOT there. Never
  present one as evidence of opportunity. If every signal is an absence, say
  the research found nothing actionable.
- Plain prose. No bullets, no markdown, no citation brackets."""


#: Hard ceilings on generated prose, enforced in the prompt and checked on the
#: way out. Chosen to match the instruction already in SYSTEM_PROMPT rather
#: than invented: "2-4 sentences" at ordinary sentence length is about 60
#: words, and "one or two sentences" about 30. A limit that contradicts the
#: qualitative instruction would just make the two fight.
#:
#: Enforced by regenerating, never by truncating. A truncated hallucination is
#: still a hallucination, and it would be stored that way.
RATIONALE_WORD_LIMIT = 60
WHY_NOW_WORD_LIMIT = 30
HEADLINE_CHAR_LIMIT = 90


class AccountBrief(BaseModel):
    headline: str
    rationale: str
    why_now: str


def _words(text: str) -> int:
    return len((text or "").split())


def over_limit(brief: AccountBrief) -> str | None:
    """Which field exceeded its ceiling, or None. Reported, not trimmed."""
    if _words(brief.rationale) > RATIONALE_WORD_LIMIT:
        return f"rationale {_words(brief.rationale)}w > {RATIONALE_WORD_LIMIT}"
    if _words(brief.why_now) > WHY_NOW_WORD_LIMIT:
        return f"why_now {_words(brief.why_now)}w > {WHY_NOW_WORD_LIMIT}"
    if len(brief.headline) > HEADLINE_CHAR_LIMIT:
        return f"headline {len(brief.headline)}c > {HEADLINE_CHAR_LIMIT}"
    return None


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


def format_signals(signals: list[ExtractedSignal]) -> str:
    """The signal set as the model sees it.

    Strongest first so the ordering itself carries the weighting, and absences
    labelled inline — a model reading a flat list has no other way to tell
    "no CRM activity, confidence 9" from a confident positive finding.
    """
    ordered = sorted(
        signals,
        key=lambda s: (s.is_absence or s.contested, -s.confidence, s.signal_type.value),
    )

    lines: list[str] = []
    for signal in ordered:
        if signal.is_absence:
            marker = " [ABSENCE — nothing was found]"
        elif signal.contested:
            # Withheld from the score, so it must not reappear as the basis of
            # the prose. Handing it over unmarked would launder a claim the
            # pipeline already refused to act on.
            marker = " [DISPUTED — the evidence says nothing was found; do not treat as a positive finding]"
        else:
            marker = ""
        lines.append(
            f"- {signal_label(signal.signal_type)} (confidence "
            f"{signal.confidence}/10){marker}\n"
            f"  Why it matters: {signal.rationale}\n"
            f"  Evidence: {signal.evidence}"
        )
    return "\n".join(lines)


def brief_sources(signals: list[ExtractedSignal]) -> list[SourceRef]:
    """Deduped sources across the signals the brief was written from,
    strongest signal first so the most relevant citation leads."""
    ordered = sorted(signals, key=lambda s: (s.is_absence or s.contested, -s.confidence))
    seen: set[str] = set()
    refs: list[SourceRef] = []
    for signal in ordered:
        for ref in signal.sources:
            if ref.url in seen:
                continue
            seen.add(ref.url)
            refs.append(ref)
    return refs


async def _attempt(account_name: str, signals: list[ExtractedSignal]) -> AccountBrief | None:
    """One generation attempt. None on any failure."""
    try:
        response = await _get_client().messages.parse(
            model=get_settings().extraction_model,
            max_tokens=1024,
            # `low` is the determinism lever on this model. `temperature` was
            # removed on Claude Opus 5 and returns a 400, so the usual
            # "turn the temperature down" is not available; lower effort is
            # the documented replacement, and this task is constrained
            # rewriting rather than reasoning.
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Account: {account_name}\n\n"
                        f"Extracted signals:\n{format_signals(signals)}"
                    ),
                }
            ],
            output_format=AccountBrief,
        )
    except Exception as exc:  # noqa: BLE001 - the caller degrades, never fails
        log.warning("brief failed", account=account_name, error=str(exc)[:300])
        return None

    if response.stop_reason == "refusal":
        log.warning("brief refused", account=account_name)
        return None

    return response.parsed_output


async def write_brief(
    account_name: str, signals: list[ExtractedSignal]
) -> AccountBrief | None:
    """Elaborate the stored signals into a brief. None on any failure.

    Length is enforced by regenerating once, then giving up — never by
    truncating. A brief cut mid-sentence reads as a bug, and an over-long one
    that got trimmed still has whatever made it over-long stored behind it.
    """
    if not signals:
        return None

    brief = await _attempt(account_name, signals)
    if brief is None:
        return None

    breach = over_limit(brief)
    if breach is None:
        return brief

    log.warning("brief over length; regenerating", account=account_name, breach=breach)
    retried = await _attempt(account_name, signals)
    if retried is None:
        return None

    breach = over_limit(retried)
    if breach is not None:
        # Returned anyway, and recorded. The caller wanted prose; refusing to
        # produce any because it ran ten words long would be the worse trade,
        # and the log is what makes a drifting prompt visible.
        log.warning("brief still over length after retry", account=account_name, breach=breach)
    return retried
