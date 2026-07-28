"""Parse the Opportunity-Signal Research cell into a score + rationale.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

#: Matches one complete top-level `{...}` object even inside a truncated
#: array tail, since the array itself has no nesting to confuse it.
_ITEM_PATTERN = re.compile(r"\{[^{}]*\}")


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


def parse_cell(text: str | None) -> ParsedCell:
    """Extract one account's score, signal type, and rationale. Never raises."""
    if not text or not str(text).strip():
        return ParsedCell(raw=text or "", needs_review=True)

    text = str(text)
    items = _extract_items(text)
    if not items:
        # No structured signal — Rox's prose explanation of why, preserved
        # as-is since there is nothing to score.
        return ParsedCell(rationale=text.strip(), raw=text, needs_review=True)

    driver = max(items, key=lambda i: _as_score(i.get("score")) or -1)
    raw_score = _as_score(driver.get("score"))

    rationale = "\n\n".join(
        str(item["evidence"]).strip() for item in items if item.get("evidence")
    ) or None

    return ParsedCell(
        score=None if raw_score is None else max(0, min(100, round(raw_score * 10))),
        signal_type=_slug(driver.get("signal")),
        rationale=rationale,
        raw=text,
        needs_review=raw_score is None,
    )
