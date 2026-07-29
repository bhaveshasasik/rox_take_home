"""The parser sits between an LLM's free text and our scoring, so malformed
input is the expected case, not the exception.

Fixtures below mirror the live "Opportunity-Signal Research" cell shapes
captured from the org on 2026-07-27: a JSON array of
`{"signal", "evidence", "score"}` objects, or bare prose when nothing was
found. Cells are also stored capped at ~300 chars with a trailing "...", so a
multi-item array is frequently truncated mid-object."""


from app.services.parsing import parse_cell, parse_signals

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


class TestParseSignals:
    """`parse_signals` backs the detail screen's research and score breakdown,
    so it must survive exactly the malformed input `parse_cell` already does."""

    def test_extracts_each_signal_with_normalized_score(self):
        signals = parse_signals(MULTI_SIGNAL)
        assert [(s.signal, s.score) for s in signals] == [
            ("Growth / Expansion", 70),
            ("Trigger Events", 60),
        ]
        assert signals[0].evidence == "HP Q2 FY26 net revenue $14.4B."

    def test_strongest_signal_first(self):
        """The headline score is the highest signal, so it must lead."""
        signals = parse_signals(
            '[{"signal":"Champion & Engagement","evidence":"e","score":2},'
            '{"signal":"Growth / Expansion","evidence":"e","score":9}]'
        )
        assert [s.signal for s in signals] == ["Growth / Expansion", "Champion & Engagement"]
        assert signals[0].score == 90

    def test_headline_score_matches_the_top_signal(self):
        """The breakdown must actually explain the number shown above it."""
        assert parse_signals(MULTI_SIGNAL)[0].score == parse_cell(MULTI_SIGNAL).score

    def test_recovers_signals_from_a_truncated_cell(self):
        """The live 300-char cap cuts mid-object; a JSON parse would yield none."""
        signals = parse_signals(TRUNCATED)
        assert len(signals) == 1
        assert signals[0].signal == "Growth / Expansion"
        assert signals[0].score == 60

    def test_prose_cell_yields_no_signals(self):
        assert parse_signals(PROSE_FALLBACK) == []

    def test_empty_input_yields_no_signals(self):
        for value in (None, "", "   "):
            assert parse_signals(value) == []

    def test_never_raises_on_junk(self):
        for junk in ("!!!", "\x00\x01", "{" * 50, "[", "a" * 5000):
            assert parse_signals(junk) is not None

    def test_scores_stay_in_range(self):
        signals = parse_signals(
            '[{"signal":"a","evidence":"e","score":100},'
            '{"signal":"b","evidence":"e","score":-5}]'
        )
        assert all(0 <= s.score <= 100 for s in signals)

    def test_missing_score_is_none_not_zero(self):
        """A missing score is unknown, not a zero-strength signal."""
        signals = parse_signals('[{"signal":"Growth / Expansion","evidence":"e"}]')
        assert signals[0].score is None


#: The shape `output.text` actually returns — a conclusion, then bulleted
#: evidence, each bullet carrying its own confidence. Captured live on
#: 2026-07-28 from the "Opportunity-Signal Research" column.
NARRATIVE = """Conclusion: CRM shows an active internal champion and multi-threaded HR engagement at Google.

Evidence

- Internal champion / engaged contact: Senior Director, Human Resources Thomas Ryan is marked "engaged" in the CRM. [\\[1\\]](rox://contact/511bc82c)
  - Confidence: 7 (clear single strong indicator of a working relationship)

- Growth / Expansion — recent revenue disclosure: Wells Fargo reported total revenue of $22,622 million for the quarter ended June 30, 2026. [\\[2\\]](https://example.com/earnings.pdf)
  - Confidence: 4 — single authoritative data point but no corroborating internal signals
"""

#: Some cells leave the confidence line unindented, where a naive split reads
#: it as a signal in its own right.
NARRATIVE_FLAT_CONFIDENCE = """Evidence

- Champion & Engagement: CRM shows a named champion at the account.
- Confidence: 4 — single, concrete CRM record but no supporting communications.
"""

#: Others write it inline, lowercase and parenthesised.
NARRATIVE_INLINE_CONFIDENCE = """Conclusion (top-line signals)
- Growth / Expansion: Evidence of material business expansion (confidence 6).
  - Citi reports Q2 2026 net income $5.8B on revenues $24.8B.
"""

#: `Confidence` measures certainty in the claim, not opportunity strength.
NARRATIVE_ABSENT = """Evidence

- No CRM / internal communications evidence for Buying Intent or Champion signals.
  - Confidence: 8 — exhaustive search of available account data returned nothing.
"""


class TestNarrativeSignals:
    """`output.text` is markdown, not JSON — this is the majority live shape."""

    def test_extracts_each_bullet_with_its_confidence(self):
        signals = parse_signals(NARRATIVE)
        assert [(s.signal, s.score) for s in signals] == [
            ("Internal champion / engaged contact", 70),
            ("Growth / Expansion", 40),
        ]

    def test_evidence_excludes_the_citation_footnote(self):
        top = parse_signals(NARRATIVE)[0]
        assert "rox://contact" not in top.evidence
        assert top.evidence.startswith("Senior Director")

    def test_multiple_signals_survive(self):
        """The 300-char cell_value leaves at most one; the full text keeps all."""
        assert len(parse_signals(NARRATIVE)) == 2

    def test_unindented_confidence_attaches_to_the_signal_above(self):
        signals = parse_signals(NARRATIVE_FLAT_CONFIDENCE)
        assert [s.signal for s in signals] == ["Champion & Engagement"]
        assert signals[0].score == 40

    def test_inline_lowercase_confidence_is_read(self):
        signals = parse_signals(NARRATIVE_INLINE_CONFIDENCE)
        assert signals[0].signal == "Growth / Expansion"
        assert signals[0].score == 60

    def test_absence_in_the_evidence_also_scores_zero(self):
        """The category name stays positive while the body carries the
        negation — "Growth / Expansion: No internal evidence was found" with
        Confidence 10 is maximum certainty that nothing is there, and scoring
        it 100 made a greenfield account the highest-rated in the pipeline."""
        cell = (
            "Evidence\n\n"
            "- Growth / Expansion: No internal evidence (hiring, usage, revenue) "
            "was found in CRM emails/meetings/notes.\n"
            "  - Confidence: 10\n"
        )
        signals = parse_signals(cell)
        assert signals[0].signal == "Growth / Expansion"
        assert signals[0].score == 0

    def test_an_absent_signal_scores_zero_not_high(self):
        """High confidence in "nothing found" is the weakest possible signal —
        scoring it 80 would invert the meaning."""
        signals = parse_signals(NARRATIVE_ABSENT)
        assert signals[0].score == 0

    def test_parse_cell_scores_from_the_narrative(self):
        parsed = parse_cell(NARRATIVE)
        assert parsed.score == 70, "headline score is the strongest signal"
        assert parsed.signal_type == "internal_champion_engaged_contact"
        assert parsed.needs_review is False
        assert "Senior Director" in parsed.rationale

    def test_narrative_with_no_confidence_stays_unscored(self):
        parsed = parse_cell("Some prose with no bullets and no confidence at all.")
        assert parsed.score is None
        assert parsed.needs_review is True

    def test_json_still_wins_when_present(self):
        """A structured cell must not fall through to the markdown reader."""
        assert [s.signal for s in parse_signals(MULTI_SIGNAL)] == [
            "Growth / Expansion",
            "Trigger Events",
        ]


class TestMidSentenceAbsence:
    """A negation can keep a positive-sounding label and state the absence in
    the body — "Internal CRM communications returned no results"."""

    def test_absence_stated_mid_sentence_scores_zero(self):
        cell = (
            "Evidence\n\n"
            "- Champion & Engagement — internal CRM communications: returned no "
            "results for relevance searches across the account.\n"
            "  - Confidence: 8\n"
        )
        assert parse_signals(cell)[0].score == 0

    def test_a_positive_finding_is_not_zeroed_by_a_buried_phrase(self):
        """Only the opening clause decides polarity; scanning the whole body
        would zero genuinely positive findings that mention an absence later."""
        cell = (
            "Evidence\n\n"
            "- Champion & Engagement: Presence of a C-level contact in CRM indicates "
            "an executive relationship, though there are no meetings logged this "
            "quarter and no internal notes beyond the initial record.\n"
            "  - Confidence: 4\n"
        )
        assert parse_signals(cell)[0].score == 40


class TestCategoryGate:
    """The column's prompt names six categories to score. Rox pads well past
    that brief, and because `Confidence` measures certainty rather than
    strength, a maximum-confidence firmographic outscored every real signal."""

    FIRMOGRAPHIC = (
        "Evidence\n\n"
        "- Headquarters: Palo Alto, California, per the company website.\n"
        "  - Confidence: 10\n\n"
        "- Growth / Expansion: Deliveries up 14% year over year (Q2 2026).\n"
        "  - Confidence: 6\n"
    )

    def test_out_of_category_signals_are_dismissed(self):
        signals = parse_signals(self.FIRMOGRAPHIC)
        assert [s.signal for s in signals] == ["Growth / Expansion"]

    def test_the_score_comes_from_a_real_signal_not_a_firmographic(self):
        """Tesla scored 100 on "Headquarters" before this gate existed."""
        assert parse_cell(self.FIRMOGRAPHIC).score == 60

    def test_all_six_categories_are_recognised(self):
        for label in (
            "Growth / Expansion",
            "Buying Intent (meetings cadence increase)",
            "Trigger Events",
            "Champion & Engagement",
            "Competitive Displacement",
            "Whitespace / Cross-Sell",
        ):
            cell = f"Evidence\n\n- {label}: something happened.\n  - Confidence: 7\n"
            assert parse_signals(cell), f"{label!r} should be scoreable"

    def test_a_cell_with_only_firmographics_yields_nothing(self):
        cell = (
            "Evidence\n\n"
            "- Official website: example.com\n  - Confidence: 10\n\n"
            "- Headcount: 97,000 employees.\n  - Confidence: 9\n"
        )
        assert parse_signals(cell) == []
        assert parse_cell(cell).score is None


class TestInlineConfidence:
    """Rox often writes the confidence at the end of the claim rather than as
    a sub-bullet, where it leaks into the rendered evidence and rationale."""

    INLINE = (
        "Supporting evidence\n"
        "- Growth / Expansion: deliveries up 14% year over year. Confidence: 9.\n"
        "- Buying Intent: no meetings or notes in the workspace. Confidence: 9.\n"
    )

    def test_confidence_is_read_from_the_inline_form(self):
        assert parse_signals(self.INLINE)[0].score == 90

    def test_confidence_is_stripped_from_the_evidence(self):
        top = parse_signals(self.INLINE)[0]
        assert top.evidence == "deliveries up 14% year over year"
        assert "onfidence" not in top.evidence

    def test_rationale_does_not_repeat_identical_evidence(self):
        """When every category comes back empty the same sentence repeats once
        per category, which made the rationale unreadable."""
        empty = "".join(
            f"- {cat}: none found in available internal data. Confidence: 9.\n"
            for cat in ("Growth / Expansion", "Buying Intent", "Trigger Events")
        )
        rationale = parse_cell("Supporting evidence\n" + empty).rationale
        assert rationale.count("none found in available internal data") == 1


class TestBulletsWhoseOnlyColonIsTheConfidence:
    """When the inline confidence is the only `: ` on the line, splitting
    label from evidence before stripping it left the evidence as a bare digit."""

    CELL = (
        "Supporting evidence\n"
        "- Full-year 2026 outlook cited targeting ~7% net revenue growth. Confidence: 7\n"
    )

    def test_evidence_is_the_claim_not_the_confidence_digit(self):
        top = parse_signals(self.CELL)[0]
        assert top.evidence != "7"
        assert "net revenue growth" in top.evidence

    def test_the_score_still_comes_from_that_confidence(self):
        assert parse_signals(self.CELL)[0].score == 70

    def test_rationale_prefers_what_was_found_over_what_was_not(self):
        """An absence beside a real signal is noise in the rationale."""
        cell = (
            "Supporting evidence\n"
            "- Growth / Expansion: deliveries up 14%. Confidence: 8\n"
            "- Buying Intent: no meetings or notes found. Confidence: 9\n"
        )
        rationale = parse_cell(cell).rationale
        assert "deliveries up 14%" in rationale
        assert "no meetings" not in rationale

    def test_absences_are_used_when_nothing_positive_was_found(self):
        cell = (
            "Supporting evidence\n"
            "- Growth / Expansion: no evidence found. Confidence: 9\n"
        )
        assert "no evidence found" in parse_cell(cell).rationale

    def test_parenthesised_and_fractional_confidence_are_stripped(self):
        """Rox writes it three ways: trailing, "8/10", and "(High confidence: 8)"."""
        for line, expected in [
            ("- Trigger Events: Strategic M&A and partnerships (High confidence: 8)",
             "Strategic M&A and partnerships"),
            ("- Growth / Expansion: no revenue signals present. Confidence: 8/10",
             "no revenue signals present"),
        ]:
            top = parse_signals("Supporting evidence\n" + line + "\n")[0]
            assert top.evidence == expected, top.evidence
            assert "onfidence" not in top.evidence
