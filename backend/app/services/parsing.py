"""Parse the Opportunity-Signal Research cell into a score + rationale.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

#: Matches one complete top-level `{...}` object even inside a truncated
#: array tail, since the array itself has no nesting to confuse it.
_ITEM_PATTERN = re.compile(r"\{[^{}]*\}")

#: A top-level markdown bullet. Sub-bullets are indented, so anchoring to an
#: unindented `-` keeps each signal and its own `Confidence:` line together.
_BULLET_SPLIT = re.compile(r"(?m)^-[ \t]+")

#: `Confidence: 7`, `Confidence — 7`, `(confidence 6)`. Rox renders this
#: inconsistently across cells, so match loosely but require the number to sit
#: within a few characters, or prose like "high confidence in the above" would
#: pull an unrelated digit.
_CONFIDENCE = re.compile(r"(?i)confidence[^0-9\n]{0,4}(10|\d)(?!\d)")

#: A bullet that *is* the confidence line rather than a signal. Some cells
#: leave it unindented, where it would otherwise parse as a signal called
#: "Confidence" carrying the previous bullet's score.
_CONFIDENCE_ONLY = re.compile(r"(?i)^confidence\b")

#: An explicitly absent signal. `Confidence` measures certainty in the claim,
#: not the strength of the opportunity — "No buying-intent evidence found"
#: with Confidence 8 means we are *sure there is nothing*, which is the
#: weakest possible signal. Rox's own JSON format scores these 0; matching
#: that keeps both shapes consistent.
_ABSENT = re.compile(
    r"(?i)^(no|none|not found|insufficient|lack of|absent)\b|not found|no evidence"
)

#: `[\[1\]](rox://contact/…)` — citation footnotes, stripped from display text.
_CITATION = re.compile(r"\[\\?\[\d+\\?\]\]\([^)]*\)")

#: The label ends at the first `: ` or ` — `, whichever comes first:
#: "Growth / Expansion — recent revenue disclosure: <evidence>"
#: "Internal champion / engaged contact: <evidence>"
_LABEL_SPLIT = re.compile(r"\s+—\s+|:\s+")


def _markdown_signals(text: str) -> list[ParsedSignal]:
    """Signals from the narrative format Rox actually returns.

    `output.text` is markdown — a `Conclusion:` line, then an `Evidence`
    section of bullets, each carrying its own `Confidence: N`. This is the
    richest shape available: the ~300-char `cell_value` truncates it to a
    single fragment, so parsing the full text is what makes multi-signal
    accounts visible at all.
    """
    signals: list[ParsedSignal] = []

    for block in _BULLET_SPLIT.split(text)[1:]:
        confidence = _CONFIDENCE.search(block)
        # The first line holds the claim; the Confidence line follows it.
        head = _CITATION.sub("", block.split("\n", 1)[0]).strip().rstrip(".")
        if not head:
            continue

        # A stray unindented confidence line belongs to the signal above it,
        # not to a new one.
        if _CONFIDENCE_ONLY.match(head):
            if signals and signals[-1].score is None and confidence:
                signals[-1].score = _normalize_score(float(confidence.group(1)))
            continue

        parts = _LABEL_SPLIT.split(head, maxsplit=1)
        label = parts[0].strip() if parts else head
        evidence = parts[1].strip() if len(parts) > 1 else head

        # Check the evidence too, not just the label. Rox often keeps the
        # category name positive and puts the negation in the body —
        # "Growth / Expansion: No internal evidence was found" with
        # Confidence 10 is maximum certainty that nothing is there.
        if _ABSENT.search(label) or _ABSENT.search(evidence):
            score = 0
        elif confidence:
            score = _normalize_score(float(confidence.group(1)))
        else:
            score = None

        signals.append(
            ParsedSignal(signal=label or None, evidence=evidence or None, score=score)
        )

    return signals


@dataclass
class ParsedCell:
    #: normalized to 0-100
    score: int | None = None
    signal_type: str | None = None
    rationale: str | None = None
    needs_review: bool = False
    raw: str = ""


def _as_score(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slug(name: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return text or "general_signal"


def _extract_items(text: str) -> list[dict]:
    """Every signal object in the cell, tolerating mid-array truncation."""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    items: list[dict] = []
    for match in _ITEM_PATTERN.finditer(text):
        try:
            obj = json.loads(match.group())
        except ValueError:
            continue
        if isinstance(obj, dict):
            items.append(obj)
    return items


@dataclass
class ParsedSignal:
    """One signal object from a research cell, normalized for display."""

    signal: str | None = None
    evidence: str | None = None
    #: normalized to 0-100, same scale as `Opportunity.qualification_score`
    score: int | None = None


def parse_signals(text: str | None) -> list[ParsedSignal]:
    """Every signal in a research cell, scored on the 0-100 scale.

    Exposed because the raw cell is not renderable: Rox truncates long cells
    mid-array, so `cell_value` is frequently invalid JSON that only the
    tolerant reader in `_extract_items` can recover. A client parsing it
    directly would show a broken blob, so the parsing stays here and the
    result is what crosses the wire.
    """
    if not text or not str(text).strip():
        return []

    # Structured JSON first — when Rox returns it, it is unambiguous. The
    # markdown reader is the fallback because `{...}` never appears in it.
    if not _extract_items(str(text)):
        return _sorted(_markdown_signals(str(text)))

    signals = []
    for item in _extract_items(str(text)):
        raw_score = _as_score(item.get("score"))
        signals.append(
            ParsedSignal(
                signal=str(item["signal"]).strip() if item.get("signal") else None,
                evidence=str(item["evidence"]).strip() if item.get("evidence") else None,
                score=None if raw_score is None else _normalize_score(raw_score),
            )
        )
    return _sorted(signals)


def _sorted(signals: list[ParsedSignal]) -> list[ParsedSignal]:
    """Strongest first — the order that explains the headline score, since the
    opportunity takes the highest-scoring signal. Unscored sort last."""
    return sorted(
        signals, key=lambda s: -1 if s.score is None else s.score, reverse=True
    )


def _normalize_score(raw: float) -> int:
    return max(0, min(100, round(raw * 10)))


def parse_cell(text: str | None) -> ParsedCell:
    """Extract one account's score, signal type, and rationale. Never raises."""
    if not text or not str(text).strip():
        return ParsedCell(raw=text or "", needs_review=True)

    text = str(text)
    items = _extract_items(text)
    if not items:
        # Not JSON — try the markdown narrative, which is what `output.text`
        # actually contains and which carries a `Confidence:` per signal.
        signals = _markdown_signals(text)
        scored = [s for s in signals if s.score is not None]
        if scored:
            driver = max(scored, key=lambda s: s.score or 0)
            rationale = "\n\n".join(s.evidence for s in signals if s.evidence) or None
            return ParsedCell(
                score=driver.score,
                signal_type=_slug(driver.signal),
                rationale=rationale,
                raw=text,
                # a cell whose every signal is an explicit absence is scoreable
                # but not qualified; the threshold filters it, not this flag
                needs_review=False,
            )

        # Nothing structured at all — Rox's prose explanation of why,
        # preserved as-is since there is nothing to score.
        return ParsedCell(rationale=text.strip(), raw=text, needs_review=True)

    driver = max(items, key=lambda i: _as_score(i.get("score")) or -1)
    raw_score = _as_score(driver.get("score"))

    rationale = "\n\n".join(
        str(item["evidence"]).strip() for item in items if item.get("evidence")
    ) or None

    return ParsedCell(
        score=None if raw_score is None else _normalize_score(raw_score),
        signal_type=_slug(driver.get("signal")),
        rationale=rationale,
        raw=text,
        needs_review=raw_score is None,
    )
