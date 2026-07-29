"""Rewrite `Opportunity.signal_type` from free text to the `SignalType` enum.

`signal_type` was slugified from whatever category label Rox's research happened
to emit, an open vocabulary. Of the 21 live opportunities, 9 hold values that
cannot serve as a grouping key: `trigger_event` and `trigger_events` as separate
values, and four rows where an entire sentence became the slug —
`search_across_internal_meetings_and_emails_for_cisco_2019_01_01_through...`.
`/reporting/signal-performance` groups on this column, so today it reports
roughly one bucket per account.

The mapping is taken from each opportunity's **own** research, not from a
string-matching table. Every opportunity links to the artifact it was created
from; that artifact is extracted (if it has not been already), and the driver
signal of that extraction supplies the enum value. Anything else would be
guessing at the label rather than reading the evidence.

Deliberate choices:

* **Extraction is per-linked-artifact.** The 21 linked artifacts are from older
  runs and are not the ones a recent `extract_run` covered, so this pays for
  them. `--extract` is required to spend that; without it the script reports
  what it cannot map rather than silently skipping.
* **Only `signal_type` changes.** Scores, rationales and decisions are left
  alone — this is a vocabulary repair, not a rescore. `scripts.backfill_truncated_research`
  is the one that recomputes scores.
* **Idempotent.** A row already holding a valid enum value is left untouched,
  and extraction is skipped for artifacts already covered at this schema
  version.
* **Decided opportunities are still rewritten**, unlike the rescore script.
  Relabelling which category a signal belonged to does not change the evidence
  a reviewer acted on, and leaving them behind would keep the reporting broken
  for exactly the rows reporting cares about most.

    .venv/bin/python -m scripts.backfill_signal_types --dry-run
    .venv/bin/python -m scripts.backfill_signal_types --extract --dry-run
    .venv/bin/python -m scripts.backfill_signal_types --extract
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Account, Opportunity, OpportunityResearchLink, ResearchArtifact
from app.signals.extraction import extract_artifact
from app.signals.models import ArtifactExtraction, ExtractedSignalRow
from app.signals.schema import SIGNAL_LABELS
from app.signals.scoring import score_signals
from app.signals.service import _to_signal


async def _driver_for(session, opportunity_id: str) -> str | None:
    """The enum value implied by this opportunity's own extracted research."""
    rows = (
        (
            await session.execute(
                select(ExtractedSignalRow)
                .join(
                    ArtifactExtraction,
                    ExtractedSignalRow.extraction_id == ArtifactExtraction.id,
                )
                .join(
                    OpportunityResearchLink,
                    OpportunityResearchLink.artifact_id == ArtifactExtraction.artifact_id,
                )
                .where(OpportunityResearchLink.opportunity_id == opportunity_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    scored = score_signals([_to_signal(r) for r in rows])
    if scored.signal_type is not None:
        return scored.signal_type.value

    # Every signal was an absence or out-of-category. The opportunity exists,
    # so something scored it once; `other` is the honest label rather than
    # leaving the old sentence-slug in place.
    return "other"


async def backfill(*, dry_run: bool, extract: bool) -> None:
    await init_db()

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Opportunity, Account.name)
                .join(Account, Opportunity.account_id == Account.id)
                .order_by(Opportunity.created_at)
            )
        ).all()

        if extract:
            pending = (
                await session.execute(
                    select(ResearchArtifact, Account.name)
                    .join(Account, ResearchArtifact.account_id == Account.id)
                    .join(
                        OpportunityResearchLink,
                        OpportunityResearchLink.artifact_id == ResearchArtifact.id,
                    )
                    .where(ResearchArtifact.cell_value.is_not(None))
                    .distinct()
                )
            ).all()
            print(f"extracting {len(pending)} linked artifact(s)...")
            for artifact, account_name in pending:
                if dry_run:
                    print(f"   would extract {account_name} ({len(artifact.cell_value)}ch)")
                    continue
                _record, outcome = await extract_artifact(session, artifact, account_name)
                await session.commit()
                print(f"   {account_name}: {outcome}")

        changed = unchanged = unmappable = 0
        print(f"\n{'account':<24} {'from':<38} -> to")
        print("-" * 86)

        for opportunity, account_name in rows:
            current = opportunity.signal_type or ""
            target = await _driver_for(session, opportunity.id)

            if target is None:
                unmappable += 1
                print(f"{account_name[:24]:<24} {current[:38]:<38} -> (no extraction)")
                continue
            if target == current:
                unchanged += 1
                continue

            changed += 1
            print(f"{account_name[:24]:<24} {current[:38]:<38} -> {target}")
            if not dry_run:
                opportunity.signal_type = target

        if not dry_run:
            await session.commit()

        print(
            f"\n{'would change' if dry_run else 'changed'}: {changed}   "
            f"already correct: {unchanged}   unmappable: {unmappable}"
        )
        if unmappable and not extract:
            print("\nrun with --extract to extract the linked artifacts first")

        after = (await session.execute(select(Opportunity.signal_type))).scalars().all()
        bad = [s for s in after if s not in SIGNAL_LABELS]
        print(f"values outside the enum after this run: {len(bad)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--extract",
        action="store_true",
        help="extract linked artifacts first (makes LLM calls)",
    )
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run, extract=args.extract))


if __name__ == "__main__":
    main()
