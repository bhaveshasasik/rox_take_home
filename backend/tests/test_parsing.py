"""The parser sits between an LLM's free text and our scoring, so malformed
input is the expected case, not the exception.

Fixtures below mirror the live "Opportunity-Signal Research" cell shapes
captured from the org on 2026-07-27: a JSON array of
`{"signal", "evidence", "score"}` objects, or bare prose when nothing was
found. Cells are also stored capped at ~300 chars with a trailing "...", so a
multi-item array is frequently truncated mid-object."""

import pytest

from app.services.parsing import parse_cell

SINGLE_SIGNAL = (
    '[{"signal":"Growth / Expansion","evidence":"Trailing-12-month revenue '
    'surpassed $100B (Tesla Q2 2026 update).","score":8}]'
)

MULTI_SIGNAL = (
    '[{"signal":"Growth / Expansion","evidence":"HP Q2 FY26 net revenue $14.4B.",'
    '"score":7},{"signal":"Trigger Events","evidence":"New CEO appointed in March.",'
    '"score":6}]'
)

NO_SIGNAL = '[{"signal":"none","evidence":"No verifiable sales signals found in the available data.","score":0}]'

# A 300-char cap cutting an object in half, exactly as observed live.
TRUNCATED = (
    '[{"signal":"Growth / Expansion","evidence":"Company scale: trailing '
    'twelve-month net sales $742.8B.","score":6},{"signal":"Growth / Expansion",'
    '"evidence":"Global headcount 1.57M employees as of 2026-03-31, expanding '
    'AWS footprint across multiple new regions and data cent'
)

PROSE_FALLBACK = (
    "Public signals show strong AI-driven revenue growth and an AI strategic "
    "initiative at Dell Technologies; no account-internal CRM content was "
    "available to surface buying-intent signals."
)


class TestParseCell:
    def test_single_signal(self):
        p = parse_cell(SINGLE_SIGNAL)
        assert p.score == 80
        assert p.signal_type == "growth_expansion"
        assert "Tesla" in p.rationale
        assert not p.needs_review

    def test_multi_signal_picks_the_highest_scoring_as_driver(self):
        p = parse_cell(MULTI_SIGNAL)
        assert p.score == 70
        assert p.signal_type == "growth_expansion"
        # both evidences are kept, not just the driver's
        assert "HP Q2 FY26" in p.rationale
        assert "New CEO" in p.rationale

    def test_zero_to_ten_scale_is_normalized(self):
        p = parse_cell(SINGLE_SIGNAL)
        assert p.score == 80  # 8 * 10, not 8

    def test_none_signal_scores_zero(self):
        p = parse_cell(NO_SIGNAL)
        assert p.score == 0
        assert p.signal_type == "none"
        assert not p.needs_review

    def test_truncated_array_salvages_complete_objects(self):
        p = parse_cell(TRUNCATED)
        assert p.score == 60
        assert "Company scale" in p.rationale
        # the cut-off second object must not appear
        assert "data cent" not in p.rationale

    def test_prose_fallback_has_no_score(self):
        p = parse_cell(PROSE_FALLBACK)
        assert p.score is None
        assert p.needs_review
        assert p.rationale == PROSE_FALLBACK

    def test_empty_flags_review(self):
        for value in (None, "", "   "):
            p = parse_cell(value)
            assert p.needs_review
            assert p.score is None

    def test_never_raises_on_junk(self):
        for junk in ("!!!", "\x00\x01", "{" * 50, "[", "a" * 5000):
            assert parse_cell(junk) is not None

    def test_signal_type_defaults_when_missing(self):
        p = parse_cell('[{"evidence":"Something happened.","score":5}]')
        assert p.signal_type == "general_signal"

    def test_result_stays_in_range(self):
        p = parse_cell('[{"signal":"x","evidence":"e","score":100}]')
        assert 0 <= p.score <= 100
