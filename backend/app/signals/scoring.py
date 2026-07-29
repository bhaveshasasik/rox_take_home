"""Compose a 0-100 qualification score from typed signals.

Replaces the max-confidence-wins arithmetic in `parsing.parse_cell`. Two
things change and one deliberately does not:

- the category filter is an enum membership test rather than a regex, so
  firmographics score zero by construction
- absences are a stated field rather than an inference from three regexes
- the base is still `strongest confidence x 10`, so a score that moves between
  the old and new paths is attributable to the extraction, not to a rescaling

Every factor's contribution is recorded as it is applied. The breakdown is a
byproduct of composing the total, not a second calculation that could drift
from it.
"""

from __future__ import annotations

from app.signals.schema import (
    SCORING_TYPES,
    ExtractedSignal,
    ScoreBreakdown,
    ScoredAccount,
    ScoreFactor,
    SignalType,
    signal_label,
)

#: Points per additional distinct signal type beyond the driver. Two
#: independent categories pointing the same way is genuinely more convincing
#: than one; ten weak ones are not, hence the cap.
CORROBORATION_POINTS = 5
CORROBORATION_CAP = 15

#: Points removed when every finding is an absence, scaled down proportionally.
#:
#: Without this the composite has almost no resolution. Measured across 54 real
#: extractions: confidence clusters at 6-9, so `confidence x 10` puts any
#: account with a single real signal at 60-90, corroboration adds at most 15,
#: and nothing lands between 1 and 39. Thresholds of 1, 40 and 50 all qualified
#: the identical 37 of 54 accounts — the threshold was inert.
#:
#: Absences are the one quantity that genuinely varies (0 to 10 per account) and
#: they were already being extracted and then ignored. At 30 the spread goes
#: from 10 distinct scores to 23, and an account like Ford — one positive
#: finding surrounded by five explicit absences — drops from 80 to 55, below
#: accounts carrying six positives.
#:
#: Known bias: this is driven by how thoroughly the research enumerates
#: absences, not purely by account quality. A cell reporting all six categories
#: as empty is penalised harder than one that mentions only its single positive
#: and stays silent. Acceptable while enumeration is uniform (4-8 absences on
#: most extractions); revisit if that stops holding.
ABSENCE_PENALTY_MAX = 30


def _scoreable(signal: ExtractedSignal) -> bool:
    """Signals that can lift a score.

    An absence cannot, whatever its confidence. `Confidence` measures
    certainty in the claim, and the claim "there is nothing here" being
    certainly true is the weakest possible finding — reading it as strength is
    how an account with no CRM history at all used to score 100.

    Neither can a contested one. When the model marks a signal positive over an
    evidence span that says nothing was found, the two readings cannot both be
    right, and the safe direction is not to qualify: a false negative costs a
    reviewer nothing, a false positive puts a cold account in their queue with
    a score of 100.
    """
    return (
        not signal.is_absence
        and not signal.contested
        and signal.signal_type.value in SCORING_TYPES
        and signal.confidence > 0
    )


def _absence_penalty(signals: list[ExtractedSignal]) -> int:
    """Points to remove, proportional to how much of the research came back empty.

    Measured over *all* signals, including out-of-category context, because the
    denominator is "everything this research reported" — an account with one
    positive finding among nine absences is weaker than one with six positives
    among four, and that difference is exactly what the composite was failing
    to express.
    """
    if not signals:
        return 0
    absences = sum(1 for s in signals if s.is_absence)
    return round(ABSENCE_PENALTY_MAX * absences / len(signals))


def _disputed_evidence_factor(contested: list[ExtractedSignal]) -> ScoreFactor:
    """The breakdown line for withheld signals.

    Named `disputed_evidence` for the reader while the underlying flag stays
    `contested` — one is display text on a score, the other is a column on a
    row. Always shown when present: a signal that was withheld is part of how
    the total was reached, and omitting it would make the breakdown read as
    though the finding never existed.
    """
    types = ", ".join(sorted({signal_label(s.signal_type) for s in contested}))
    return ScoreFactor(
        name="disputed_evidence",
        points=0,
        detail=(
            f"{len(contested)} signal(s) withheld — evidence asserts nothing was "
            f"found while the signal was reported positive ({types})"
        ),
    )


def score_signals(signals: list[ExtractedSignal]) -> ScoredAccount:
    """The account's 0-100 score and the factors that produced it."""
    factors: list[ScoreFactor] = []

    contested = [s for s in signals if s.contested]

    scoreable = [s for s in signals if _scoreable(s)]
    if not scoreable:
        # Distinguish "we read the research and it says nothing is happening"
        # from "we could not read the research at all". The first is a real,
        # correct answer and needs no human; the second does.
        only_absences = bool(signals) and all(
            s.is_absence or s.contested or s.signal_type is SignalType.OTHER
            for s in signals
        )
        detail = (
            "every signal was an explicit absence or out-of-category context"
            if only_absences
            else "no signals extracted"
        )
        factors.append(ScoreFactor(name="signal_strength", points=0, detail=detail))
        if contested:
            factors.append(_disputed_evidence_factor(contested))
        return ScoredAccount(
            score=0,
            signal_type=None,
            # A contested set is not a clean "nothing here" — the model and its
            # own evidence disagree, which is exactly what a human should see.
            needs_review=bool(contested) or not only_absences,
            breakdown=ScoreBreakdown(total=0, factors=factors),
        )

    driver = max(scoreable, key=lambda s: s.confidence)
    base = driver.confidence * 10
    factors.append(
        ScoreFactor(
            name="signal_strength",
            points=base,
            detail=(
                f"{signal_label(driver.signal_type)} at confidence "
                f"{driver.confidence}/10"
            ),
        )
    )

    distinct = {s.signal_type for s in scoreable}
    extra = len(distinct) - 1
    corroboration = min(extra * CORROBORATION_POINTS, CORROBORATION_CAP)
    if corroboration:
        others = sorted(
            signal_label(t) for t in distinct if t is not driver.signal_type
        )
        factors.append(
            ScoreFactor(
                name="corroboration",
                points=corroboration,
                detail=f"also evidenced by {', '.join(others)}",
            )
        )

    absences = [s for s in signals if s.is_absence]
    penalty = _absence_penalty(signals)
    if absences:
        factors.append(
            ScoreFactor(
                name="evidence_gaps",
                points=-penalty,
                detail=(
                    f"{len(absences)} of {len(signals)} findings were explicit "
                    f"absences — research looked and found nothing there"
                ),
            )
        )

    context = [s for s in signals if s.signal_type is SignalType.OTHER]
    if context:
        factors.append(
            ScoreFactor(
                name="out_of_scope",
                points=0,
                detail=f"{len(context)} out-of-category finding(s) dismissed",
            )
        )

    if contested:
        factors.append(_disputed_evidence_factor(contested))

    raw = base + corroboration - penalty
    total = max(0, min(100, raw))
    if total != raw:
        # Recorded rather than silently applied, so the factors always sum to
        # the total. A breakdown that does not add up is worse than no
        # breakdown — it invites the reader to look for the missing points.
        factors.append(
            ScoreFactor(
                name="range_cap",
                points=total - raw,
                detail=f"composed to {raw}, held to the 0-100 range",
            )
        )

    return ScoredAccount(
        score=total,
        signal_type=driver.signal_type,
        # The surviving signals still qualify this account, but a reviewer
        # should know part of the research contradicted itself.
        needs_review=bool(contested),
        breakdown=ScoreBreakdown(total=total, factors=factors),
    )
