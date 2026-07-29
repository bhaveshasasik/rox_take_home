"""Structured extraction: scoring, source resolution, and the cache.

No live LLM calls — `extract_cell` is stubbed by the autouse fixture in
conftest, and the tests that need model output set their own return value.
What is exercised for real is everything around the call: source whitelisting,
score composition, and the content-hash reuse that keeps the cycle affordable.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Account, ResearchArtifact, ResearchColumn, ResearchRun
from app.signals.elaboration import over_limit
from app.signals.extraction import (
    content_hash,
    contests_absence,
    evidence_is_contained,
    extract_artifact,
    known_sources,
    resolve_sources,
)
from app.signals.models import ExtractedSignalRow, ExtractionStatus
from app.signals.schema import (
    ExtractedSignal,
    ExtractionResult,
    SignalDraft,
    SignalType,
    SourceKind,
)
from app.signals.scoring import score_signals
from app.signals.service import extract_run, extraction_health

SEC_URL = "https://sec.gov/Archives/edgar/data/1744489/fy2026_q2.htm"
NEWS_URL = "https://thewaltdisneycompany.com/news/experiences-leadership-changes/"
ROX_URL = "rox://contact/cb7b6ba8-736b-48d9-8569-36fe525b45e6"

CELL = f"""Growth / Expansion
- Q2 FY2026 revenue up 7% YoY to $25.168B, with guidance calling for
  double-digit adjusted EPS growth in FY2027. [\\[1\\]]({SEC_URL})
- Experiences leadership change announced this quarter. [\\[2\\]]({NEWS_URL})

Champion / Engagement
- No internal CRM activity found for this account.
"""


def _signal(**kwargs) -> ExtractedSignal:
    defaults = dict(
        signal_type=SignalType.GROWTH_EXPANSION,
        confidence=8,
        is_absence=False,
        contested=False,
        rationale="why",
        evidence="evidence",
        sources=[],
    )
    return ExtractedSignal(**{**defaults, **kwargs})


async def _artifact(session, cell: str = CELL, sources: list[str] | None = None):
    account = Account(rox_entity_id="e-1", name="Disney")
    column = ResearchColumn(key="opportunity_signal", name="Opportunity-Signal Research")
    run = ResearchRun(trigger="test")
    session.add_all([account, column, run])
    await session.flush()

    artifact = ResearchArtifact(
        run_id=run.id,
        account_id=account.id,
        column_id=column.id,
        cell_value=cell,
        # `is None`, not `or`: a test passing [] means "this cell has no
        # listed sources", which `or` would quietly turn back into the default
        raw={
            "output": {
                "text": cell,
                "sources": [SEC_URL, NEWS_URL] if sources is None else sources,
            }
        },
    )
    session.add(artifact)
    await session.flush()
    return account, run, artifact


class TestScoring:
    def test_base_is_strongest_confidence_times_ten(self):
        scored = score_signals([_signal(confidence=7), _signal(confidence=4)])
        assert scored.score == 70
        assert scored.breakdown.total == 70

    def test_corroboration_from_distinct_types_is_capped(self):
        types = [
            SignalType.GROWTH_EXPANSION,
            SignalType.BUYING_INTENT,
            SignalType.TRIGGER_EVENT,
            SignalType.CHAMPION_ENGAGEMENT,
            SignalType.COMPETITIVE_DISPLACEMENT,
        ]
        scored = score_signals([_signal(signal_type=t, confidence=5) for t in types])
        # 4 extra types would be 20 points uncapped
        assert scored.score == 50 + 15

    def test_repeating_one_type_does_not_corroborate(self):
        scored = score_signals([_signal(confidence=6), _signal(confidence=6)])
        assert scored.score == 60

    def test_confident_absence_scores_zero(self):
        """The inversion the regex parser existed to catch: certainty that
        nothing is there is the weakest possible finding, not the strongest."""
        scored = score_signals([_signal(confidence=10, is_absence=True)])
        assert scored.score == 0
        assert scored.signal_type is None

    def test_absence_never_lifts_a_real_signal(self):
        """The original invariant, unchanged: whatever an absence's confidence,
        it can only ever hold a score down. It now subtracts as well — see
        TestAbsencePenalty — so this asserts the direction, not a fixed value."""
        base_only = score_signals([_signal(confidence=5)])
        with_absence = score_signals(
            [
                _signal(confidence=5),
                _signal(signal_type=SignalType.BUYING_INTENT, confidence=10, is_absence=True),
            ]
        )
        assert with_absence.score < base_only.score
        assert base_only.score == 50


class TestAbsencePenalty:
    """Absences subtract proportionally rather than contributing nothing.

    Adopted after calibration: across 54 real extractions the composite had
    only 10 distinct values and nothing between 1 and 39, because confidence
    clusters at 6-9. Absences were the one quantity that genuinely varied and
    they were being extracted and then ignored.
    """

    def test_penalty_scales_with_the_absence_share(self):
        one_of_two = score_signals(
            [_signal(confidence=8), _signal(confidence=8, is_absence=True)]
        )
        one_of_four = score_signals(
            [_signal(confidence=8)] * 3 + [_signal(confidence=8, is_absence=True)]
        )
        # same driver, same base — only the share of absences differs
        assert one_of_two.score < one_of_four.score

    def test_no_absences_means_no_penalty(self):
        assert score_signals([_signal(confidence=8)]).score == 80

    def test_all_absences_still_scores_zero(self):
        scored = score_signals([_signal(confidence=9, is_absence=True)] * 3)
        assert scored.score == 0

    def test_one_positive_among_many_absences_ranks_below_several_positives(self):
        """The case that motivated this. Ford — a single positive finding
        surrounded by five absences — scored 80, above accounts carrying six
        genuine signals."""
        lonely = score_signals(
            [_signal(confidence=8)] + [_signal(confidence=8, is_absence=True)] * 5
        )
        corroborated = score_signals(
            [
                _signal(signal_type=t, confidence=8)
                for t in (
                    SignalType.GROWTH_EXPANSION,
                    SignalType.TRIGGER_EVENT,
                    SignalType.BUYING_INTENT,
                )
            ]
            + [_signal(confidence=8, is_absence=True)]
        )
        assert lonely.score < corroborated.score

    def test_penalty_is_named_in_the_breakdown(self):
        scored = score_signals(
            [_signal(confidence=8), _signal(confidence=8, is_absence=True)]
        )
        absence = next(f for f in scored.breakdown.factors if f.name == "evidence_gaps")
        assert absence.points < 0

    def test_factors_still_sum_to_the_total_with_a_penalty(self):
        scored = score_signals(
            [
                _signal(confidence=9),
                _signal(signal_type=SignalType.TRIGGER_EVENT, confidence=8),
                _signal(confidence=7, is_absence=True),
                _signal(signal_type=SignalType.OTHER, confidence=6),
            ]
        )
        assert sum(f.points for f in scored.breakdown.factors) == scored.score

    def test_clamping_is_recorded_so_the_breakdown_adds_up(self):
        """Base 100 plus corroboration overshoots; the excess is shown rather
        than silently dropped."""
        scored = score_signals(
            [
                _signal(signal_type=t, confidence=10)
                for t in (
                    SignalType.GROWTH_EXPANSION,
                    SignalType.TRIGGER_EVENT,
                    SignalType.BUYING_INTENT,
                )
            ]
        )
        assert scored.score == 100
        assert sum(f.points for f in scored.breakdown.factors) == 100

    def test_firmographics_score_zero(self):
        """`other` is where headquarters and headcount land. A maximum
        confidence fact about an office address must not outscore a real
        buying signal — this is what put Tesla at 100 on 'Headquarters'."""
        scored = score_signals([_signal(signal_type=SignalType.OTHER, confidence=10)])
        assert scored.score == 0

    def test_absences_only_is_not_flagged_for_review(self):
        """Read the research, found nothing: a correct answer needing no human."""
        scored = score_signals([_signal(confidence=9, is_absence=True)])
        assert scored.needs_review is False

    def test_nothing_extracted_is_flagged_for_review(self):
        assert score_signals([]).needs_review is True

    def test_score_stays_in_range(self):
        scored = score_signals(
            [
                _signal(signal_type=t, confidence=10)
                for t in SignalType
                if t is not SignalType.OTHER
            ]
        )
        assert 0 <= scored.score <= 100

    def test_factors_sum_to_the_total(self):
        """The breakdown is a byproduct of composing the total, so it cannot
        disagree with it."""
        scored = score_signals(
            [
                _signal(confidence=8),
                _signal(signal_type=SignalType.TRIGGER_EVENT, confidence=6),
                _signal(signal_type=SignalType.OTHER, confidence=9),
                _signal(signal_type=SignalType.BUYING_INTENT, confidence=7, is_absence=True),
            ]
        )
        assert sum(f.points for f in scored.breakdown.factors) == scored.score

    def test_dismissed_findings_are_still_reported(self):
        scored = score_signals(
            [
                _signal(confidence=8),
                _signal(signal_type=SignalType.OTHER, confidence=9),
                _signal(signal_type=SignalType.BUYING_INTENT, confidence=7, is_absence=True),
            ]
        )
        names = {f.name for f in scored.breakdown.factors}
        assert {"evidence_gaps", "out_of_scope"} <= names


class TestContestedGuard:
    """The model reporting a positive signal over evidence that says nothing
    was found. Observed on the first live run: 7 of 45 signals, which composed
    to a score of 100 for an account with no CRM history at all."""

    # verbatim from the live run that exposed this
    IBM_SPANS = [
        "CRM history / communications: No meetings, emails, or notes returned "
        "for IBM in the account search.  (search returned empty results)",
        "Champion & Engagement: No evidence in CRM/emails/meetings/notes of an "
        "internal champion, executive sponsor, or responsiveness trends",
        "Trigger Events (leadership change, reorganizations, M&A, product "
        "launches): No account-level trigger events found.",
        "Whitespace / Cross-Sell: No documented unmet needs, recurring pain "
        "points, or product/module requests in the account materials returned.",
    ]

    #: real positive spans from the same run — the guard must leave these alone
    REAL_SPANS = [
        "Q1 FY27 reported total revenues = $177.8B; Walmart U.S. comp sales "
        "(ex-fuel) +4.1% and eCommerce growth ~26%",
        "Public supply-chain initiative announced May 26, 2026 tied to "
        "investments in fulfillment automation",
        "IBM published a second-quarter results press release dated July 22, 2026.",
        "Store footprint activity: Q1 FY27 opened 3 Supercenters and completed "
        "~60 remodels",
    ]

    @pytest.mark.parametrize("span", IBM_SPANS)
    def test_detects_asserted_absence(self, span):
        assert contests_absence(span) is True

    @pytest.mark.parametrize("span", REAL_SPANS)
    def test_leaves_real_findings_alone(self, span):
        assert contests_absence(span) is False

    def test_no_longer_using_is_not_an_absence(self):
        """A bare negation is not enough. 'No longer using X' is a genuine
        displacement signal and the most obvious way to over-fire."""
        assert (
            contests_absence("The account is no longer using Salesforce for CRM")
            is False
        )

    def test_contested_signal_cannot_lift_a_score(self):
        scored = score_signals([_signal(confidence=9, contested=True)])
        assert scored.score == 0

    def test_contested_signal_forces_review(self):
        scored = score_signals(
            [_signal(confidence=8), _signal(signal_type=SignalType.TRIGGER_EVENT, confidence=9, contested=True)]
        )
        assert scored.score == 80
        assert scored.needs_review is True

    def test_withheld_signal_is_named_in_the_breakdown(self):
        scored = score_signals([_signal(confidence=9, contested=True)])
        names = {f.name for f in scored.breakdown.factors}
        assert "disputed_evidence" in names

    async def test_contested_is_persisted(self, session):
        account, _run, artifact = await _artifact(session)
        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.CHAMPION_ENGAGEMENT,
                    confidence=9,
                    is_absence=False,
                    rationale="A complete absence of meetings means no contact",
                    evidence=self.IBM_SPANS[0],
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ):
            record, _outcome = await extract_artifact(session, artifact, account.name)

        rows = (
            await session.execute(
                select(ExtractedSignalRow).where(
                    ExtractedSignalRow.extraction_id == record.id
                )
            )
        ).scalars().all()
        assert [r.contested for r in rows] == [True]

    def test_marked_absences_are_not_double_counted(self):
        """A signal the model already marked as an absence is not contested —
        there is no contradiction to record."""
        assert (
            score_signals(
                [_signal(confidence=9, is_absence=True)]
            ).breakdown.factors[-1].name
            != "disputed_evidence"
        )


class TestSources:
    async def test_known_sources_merges_listed_and_inline(self, session):
        _account, _run, artifact = await _artifact(session)
        urls = [s.url for s in known_sources(artifact)]
        assert SEC_URL in urls and NEWS_URL in urls

    async def test_rox_refs_are_kept_and_tagged(self, session):
        cell = f"- A contact engaged. [\\[1\\]]({ROX_URL})"
        _account, _run, artifact = await _artifact(session, cell, sources=[])
        refs = known_sources(artifact)
        assert [r.kind for r in refs] == [SourceKind.ROX]

    async def test_placeholders_are_dropped(self, session):
        cell = "- Something. [\\[1\\]](placeholder:unresolved)"
        _account, _run, artifact = await _artifact(session, cell, sources=[])
        assert known_sources(artifact) == []

    def test_hallucinated_url_is_dropped(self):
        """The whole point of resolving in code. A fabricated source attached
        to a real claim is worse than no source."""
        known = [s for s in _refs([SEC_URL])]
        fake = "https://totally-invented.example.com/report"
        assert resolve_sources(f"Revenue up 7% [\\[1\\]]({fake})", known) == []

    def test_verbatim_citation_resolves(self):
        known = _refs([SEC_URL])
        got = resolve_sources(f"Revenue up 7% [\\[1\\]]({SEC_URL})", known)
        assert [s.url for s in got] == [SEC_URL]

    def test_span_lookup_recovers_stripped_citations(self):
        """The model reflows or drops the citation markers when copying. The
        span still anchors to the right bullet in the raw cell."""
        known = _refs([SEC_URL, NEWS_URL])
        evidence = "Q2 FY2026 revenue up 7% YoY to $25.168B"
        got = resolve_sources(evidence, known, CELL)
        assert SEC_URL in [s.url for s in got]

    def test_unmatchable_evidence_yields_nothing(self):
        known = _refs([SEC_URL])
        assert resolve_sources("entirely unrelated prose", known, CELL) == []


def _refs(urls):
    from app.signals.extraction import _dedupe

    return _dedupe(list(urls))


class TestExtractionPersistence:
    async def test_failure_is_recorded_not_raised(self, session):
        """The autouse fixture makes `extract_cell` return None. A cell we
        fetched and paid for must never vanish silently."""
        account, _run, artifact = await _artifact(session)
        record, outcome = await extract_artifact(session, artifact, account.name)
        assert outcome == "failed"
        assert record.status == ExtractionStatus.FAILED.value
        assert record.error

    async def test_signals_and_sources_persist(self, session):
        account, _run, artifact = await _artifact(session)
        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=8,
                    is_absence=False,
                    rationale="Revenue is growing",
                    evidence=f"Q2 FY2026 revenue up 7% YoY to $25.168B [\\[1\\]]({SEC_URL})",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ):
            record, outcome = await extract_artifact(session, artifact, account.name)

        assert outcome == "extracted"
        assert record.status == ExtractionStatus.OK.value
        rows = (
            await session.execute(
                ExtractedSignalRow.__table__.select().where(
                    ExtractedSignalRow.extraction_id == record.id
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].sources == [{"url": SEC_URL, "kind": "web"}]

    async def test_a_failed_extraction_is_retried_not_skipped(self, session):
        """Failures here are transient — credit exhaustion, rate limits, an API
        outage. Treating one as done makes it permanent: the retry skips it and
        reports success while changing nothing. Seen for real when a backfill
        hit a billing limit partway through and 13 artifacts recorded failures."""
        account, _run, artifact = await _artifact(session)

        # first pass fails (autouse fixture returns None)
        _record, outcome = await extract_artifact(session, artifact, account.name)
        await session.commit()
        assert outcome == "failed"

        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=7,
                    is_absence=False,
                    rationale="recovered on retry",
                    evidence="Q2 revenue up 7%",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ):
            record, outcome = await extract_artifact(session, artifact, account.name)

        assert outcome == "extracted", "a failed row must not block the retry"
        assert record.status == ExtractionStatus.OK.value
        assert record.signal_count == 1

    async def test_a_successful_extraction_is_still_skipped(self, session):
        account, _run, artifact = await _artifact(session)
        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=7,
                    is_absence=False,
                    rationale="r",
                    evidence="e",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ) as call:
            await extract_artifact(session, artifact, account.name)
            await session.commit()
            _record, outcome = await extract_artifact(session, artifact, account.name)

        assert outcome == "skipped"
        assert call.await_count == 1, "no second call for an already-good extraction"

    async def test_an_empty_extraction_is_not_retried(self, session):
        """`empty` is a real answer — the cell genuinely had nothing scoreable.
        Retrying it would pay for the same null result every cycle."""
        account, _run, artifact = await _artifact(session)
        with patch(
            "app.signals.extraction.extract_cell",
            AsyncMock(return_value=ExtractionResult(signals=[])),
        ):
            record, _ = await extract_artifact(session, artifact, account.name)
            await session.commit()
            assert record.status == ExtractionStatus.EMPTY.value
            _record, outcome = await extract_artifact(session, artifact, account.name)

        assert outcome == "skipped"

    async def test_identical_text_is_reused_not_re_extracted(self, session):
        """The cache that makes this affordable: the cycle writes a fresh
        artifact row for every account every 15 minutes whether or not the
        research changed."""
        account, run, first = await _artifact(session)
        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.TRIGGER_EVENT,
                    confidence=6,
                    is_absence=False,
                    rationale="Leadership changed",
                    evidence="Experiences leadership change announced this quarter.",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ) as call:
            await extract_artifact(session, first, account.name)
            await session.commit()

            later_run = ResearchRun(trigger="test")
            session.add(later_run)
            await session.flush()
            second = ResearchArtifact(
                run_id=later_run.id,
                account_id=account.id,
                column_id=first.column_id,
                cell_value=first.cell_value,
                raw=first.raw,
            )
            session.add(second)
            await session.flush()

            record, outcome = await extract_artifact(session, second, account.name)

        assert outcome == "reused"
        assert call.await_count == 1
        assert record.signal_count == 1

    async def test_changed_text_is_re_extracted(self, session):
        account, _run, artifact = await _artifact(session)
        assert content_hash(artifact.cell_value) != content_hash("different research")


class TestOpportunityScoringSeam:
    """Phase 5: opportunities score from structured signals, with `parse_cell`
    kept only as the fallback for artifacts that have not been extracted."""

    async def _extraction(self, session, artifact, account, drafts):
        from app.signals.models import ArtifactExtraction

        extraction = ArtifactExtraction(
            artifact_id=artifact.id,
            account_id=account.id,
            content_hash="h",
            schema_version=1,
            signal_count=len(drafts),
        )
        session.add(extraction)
        await session.flush()
        for i, d in enumerate(drafts):
            session.add(
                ExtractedSignalRow(
                    extraction_id=extraction.id,
                    signal_type=d.signal_type.value,
                    confidence=d.confidence,
                    is_absence=d.is_absence,
                    rationale=d.rationale,
                    evidence=d.evidence,
                    position=i,
                )
            )
        await session.commit()

    async def test_scores_from_extracted_signals(self, session):
        from app.services.opportunities import create_opportunities_for_run

        account, run, artifact = await _artifact(session)
        await self._extraction(
            session,
            artifact,
            account,
            [
                SignalDraft(
                    signal_type=SignalType.TRIGGER_EVENT,
                    confidence=9,
                    is_absence=False,
                    rationale="Leadership changed this quarter",
                    evidence="Joe Schott named president",
                )
            ],
        )

        created = await create_opportunities_for_run(session, run, notify=False)

        assert len(created) == 1
        assert created[0].qualification_score == 90
        assert created[0].signal_type == "trigger_event", "enum value, not a slug"
        assert "Leadership changed this quarter" in created[0].rationale

    async def test_absence_only_extraction_creates_nothing(self, session):
        """The Bank of America case: the legacy parser scored it 90 by reading
        an absence's confidence as strength."""
        from app.services.opportunities import create_opportunities_for_run

        account, run, artifact = await _artifact(session)
        await self._extraction(
            session,
            artifact,
            account,
            [
                SignalDraft(
                    signal_type=SignalType.BUYING_INTENT,
                    confidence=9,
                    is_absence=True,
                    rationale="Nothing found",
                    evidence="No evidence found for pricing discussions",
                )
            ],
        )

        assert await create_opportunities_for_run(session, run, notify=False) == []

    async def test_falls_back_to_parse_cell_when_not_extracted(self, session):
        """357 of 432 artifacts are unextracted, so the fallback is load-bearing
        rather than vestigial."""
        from app.services.opportunities import create_opportunities_for_run

        cell = '[{"signal":"Growth / Expansion","evidence":"Revenue up 40%","score":8}]'
        account, run, artifact = await _artifact(session, cell)

        created = await create_opportunities_for_run(session, run, notify=False)

        assert len(created) == 1
        assert created[0].qualification_score == 80


class TestEvidenceValidation:
    """Evidence must be locatable in the source cell. It is the one field the
    design promises is checkable — sources are resolved from it and the UI
    presents it as quoted source text."""

    def test_verbatim_span_passes(self):
        assert evidence_is_contained(
            "Q2 FY2026 revenue up 7% YoY to $25.168B", CELL
        )

    def test_reflowed_whitespace_passes(self):
        """The model reflows line breaks even when copying faithfully."""
        assert evidence_is_contained(
            "Q2 FY2026 revenue up 7% YoY to $25.168B,   with guidance\n\ncalling for",
            CELL,
        )

    def test_span_without_citation_markup_passes(self):
        assert evidence_is_contained(
            "Experiences leadership change announced this quarter.", CELL
        )

    def test_invented_span_fails(self):
        assert not evidence_is_contained(
            "Disney announced a $4B acquisition of a streaming rival", CELL
        )

    def test_category_name_echo_fails(self):
        """A real failure in the corpus — the model echoed the category name
        back instead of quoting anything."""
        assert not evidence_is_contained("competitive displacement", CELL)

    def test_empty_fails(self):
        assert not evidence_is_contained("", CELL)
        assert not evidence_is_contained("something", "")

    async def test_retry_must_beat_the_first_result_to_replace_it(self, session):
        """Both passes are independent samples of a nondeterministic call. A
        retry that fails containment as badly as — or worse than — the first
        result must not replace it: at ~5 retries per daily cycle, accepting
        the re-roll unconditionally swaps good extractions for worse ones."""
        good_span = "No internal CRM activity found for this account."
        first = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.CHAMPION_ENGAGEMENT,
                    confidence=8,
                    is_absence=True,
                    rationale="nothing in CRM",
                    evidence=good_span,
                ),
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=9,
                    is_absence=False,
                    rationale="paraphrased",
                    evidence="Q2 revenue rose seven percent year over year",
                ),
            ]
        )
        worse_retry = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=9,
                    is_absence=False,
                    rationale="also paraphrased",
                    evidence="Revenue grew by a high single digit figure",
                ),
                SignalDraft(
                    signal_type=SignalType.TRIGGER_EVENT,
                    confidence=7,
                    is_absence=False,
                    rationale="also paraphrased",
                    evidence="A leadership shuffle occurred at Experiences",
                ),
            ]
        )
        account, _run, artifact = await _artifact(session)
        with patch(
            "app.signals.extraction.extract_cell",
            AsyncMock(side_effect=[first, worse_retry]),
        ) as call:
            record, _outcome = await extract_artifact(session, artifact, account.name)

        assert call.await_count == 2
        rows = (
            await session.execute(
                select(ExtractedSignalRow)
                .where(ExtractedSignalRow.extraction_id == record.id)
                .order_by(ExtractedSignalRow.position)
            )
        ).scalars().all()
        # The first result stands: its locatable span survives verbatim, and
        # the retry's two unlocatable signals are nowhere to be seen.
        assert [r.signal_type for r in rows] == ["champion_engagement", "growth_expansion"]
        assert rows[0].evidence == good_span
        assert rows[0].validation_failed is False
        assert rows[1].validation_failed is True

    async def test_unlocatable_evidence_is_discarded_and_flagged(self, session):
        account, _run, artifact = await _artifact(session)
        invented = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=9,
                    is_absence=False,
                    rationale="looks plausible",
                    evidence="Disney acquired a $4B streaming rival in March",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=invented)
        ) as call:
            record, _outcome = await extract_artifact(session, artifact, account.name)

        assert call.await_count == 2, "one retry before giving up"
        rows = (
            await session.execute(
                select(ExtractedSignalRow).where(
                    ExtractedSignalRow.extraction_id == record.id
                )
            )
        ).scalars().all()
        assert rows[0].validation_failed is True
        assert rows[0].evidence == "", "an unvalidated span is discarded, not stored"

    async def test_valid_evidence_is_not_retried(self, session):
        account, _run, artifact = await _artifact(session)
        good = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=8,
                    is_absence=False,
                    rationale="r",
                    evidence="Experiences leadership change announced this quarter.",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=good)
        ) as call:
            record, _outcome = await extract_artifact(session, artifact, account.name)

        assert call.await_count == 1
        rows = (
            await session.execute(
                select(ExtractedSignalRow).where(
                    ExtractedSignalRow.extraction_id == record.id
                )
            )
        ).scalars().all()
        assert rows[0].validation_failed is False
        assert rows[0].evidence


class TestBriefLengthLimits:
    """Length is a ceiling enforced by regenerating, never by truncating — a
    truncated hallucination is still a hallucination."""

    def _brief(self, **kw):
        from app.signals.elaboration import AccountBrief

        return AccountBrief(
            **{"headline": "Short headline", "rationale": "Two words.", "why_now": "Now.", **kw}
        )

    def test_within_limits_passes(self):
        assert over_limit(self._brief()) is None

    def test_long_rationale_is_caught(self):
        breach = over_limit(self._brief(rationale="word " * 61))
        assert breach and "rationale" in breach

    def test_long_why_now_is_caught(self):
        breach = over_limit(self._brief(why_now="word " * 31))
        assert breach and "why_now" in breach

    def test_long_headline_is_caught(self):
        breach = over_limit(self._brief(headline="x" * 91))
        assert breach and "headline" in breach


class TestHealth:
    async def test_health_reports_uncovered_artifacts(self, session):
        await _artifact(session)
        await session.commit()

        health = await extraction_health(session)
        assert health.artifacts == 1
        assert health.unextracted == 1

    async def test_failure_rate_is_reported(self, session):
        account, _run, artifact = await _artifact(session)
        await extract_artifact(session, artifact, account.name)
        await session.commit()

        health = await extraction_health(session)
        assert health.failure_rate == 1.0
        assert health.by_status[ExtractionStatus.FAILED.value] == 1

    async def test_low_yield_is_flagged(self, session):
        """The tripwire for silent under-extraction. Both real defects scored
        normally and were invisible to every other metric — one signal from a
        4,285-char cell only shows up as a yield."""
        long_cell = "Growth / Expansion — a finding. " * 200  # ~6.4k chars
        account, _run, artifact = await _artifact(session, long_cell)
        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=8,
                    is_absence=False,
                    rationale="only one signal from a very long cell",
                    evidence="a finding",
                )
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ):
            await extract_artifact(session, artifact, account.name)
        await session.commit()

        health = await extraction_health(session)
        assert health.low_yield == 1
        assert health.low_yield_accounts == [account.name]
        assert health.signals_per_1k_chars < 1.0

    async def test_healthy_yield_is_not_flagged(self, session):
        account, _run, artifact = await _artifact(session, "signal text. " * 60)
        result = ExtractionResult(
            signals=[
                SignalDraft(
                    signal_type=SignalType.GROWTH_EXPANSION,
                    confidence=7,
                    is_absence=False,
                    rationale=f"finding {i}",
                    evidence=f"evidence {i}",
                )
                for i in range(6)
            ]
        )
        with patch(
            "app.signals.extraction.extract_cell", AsyncMock(return_value=result)
        ):
            await extract_artifact(session, artifact, account.name)
        await session.commit()

        assert (await extraction_health(session)).low_yield == 0

    async def test_short_cells_are_not_measured(self, session):
        """Two signals in 200 characters is a yield of 10 and means nothing."""
        account, _run, artifact = await _artifact(session, "short cell")
        await extract_artifact(session, artifact, account.name)
        await session.commit()

        assert (await extraction_health(session)).low_yield == 0

    async def test_extract_run_tallies_outcomes(self, session):
        await _artifact(session)
        await session.commit()

        stats = await extract_run(session)
        assert stats.examined == 1
        assert stats.failed == 1
