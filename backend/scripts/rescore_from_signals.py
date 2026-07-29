"""Rewrite `Opportunity.qualification_score` from extracted signals.

Opportunities created before phase 5 hold a score from `parse_cell`. The detail
view now computes a factor breakdown from the extracted signals, and the two
disagree on 17 of 21 rows — one reads 80 in the header over factors summing to
71. A headline number its own visible breakdown cannot explain is worse than a
changed number, which is the same reasoning `backfill_truncated_research`
applies to its rescore.

After this, `qualification_score` *is* `score_breakdown.total`, so the pipeline
table and the detail view agree.

Deliberate choices:

* **Decided opportunities are skipped.** Someone accepted or rejected them on
  the number they were shown; rewriting it afterwards falsifies the record.
  They keep their original score and will still disagree with their breakdown —
  a smaller, older residue than rewriting history. `--include-decided` overrides
  this, which you want only if the original decisions are being discarded too.
* **`needs_review` and `signal_type` move with the score**, because all three
  come out of the same scoring pass — leaving a stale flag or a stale label
  beside a fresh score is the inconsistency this script exists to remove.
  `signal_type` only ever moves to a real driver: a pass that found nothing
  scoreable has no driver, and the stored label stands.
* **Nothing is deleted.** An opportunity rescored below
  `OPPORTUNITY_SCORE_THRESHOLD` still exists; the threshold gates creation, not
  continued existence. Those rows are reported so the drop is not silent.
* **Opportunities with no extraction are left alone**, not zeroed. No signals
  means "not extracted", which is not the same as "scored zero".

    .venv/bin/python -m scripts.rescore_from_signals --dry-run
    .venv/bin/python -m scripts.rescore_from_signals
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Account, Opportunity, OpportunityResearchLink, OpportunityStatus
from app.signals.service import scored_for_artifact


async def rescore(*, dry_run: bool, include_decided: bool) -> None:
    await init_db()
    threshold = get_settings().opportunity_score_threshold

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Opportunity, Account.name)
                .join(Account, Opportunity.account_id == Account.id)
                .order_by(Opportunity.created_at)
            )
        ).all()

        changed = unchanged = skipped_decided = no_extraction = 0
        dropped_below: list[tuple[str, int, int]] = []

        print(f"{'account':<24} {'from':>5} {'to':>5}  note")
        print("-" * 60)

        for opportunity, account_name in rows:
            if opportunity.status != OpportunityStatus.NEW.value and not include_decided:
                skipped_decided += 1
                continue

            artifact_id = (
                await session.execute(
                    select(OpportunityResearchLink.artifact_id).where(
                        OpportunityResearchLink.opportunity_id == opportunity.id
                    )
                )
            ).scalars().first()

            got = await scored_for_artifact(session, artifact_id) if artifact_id else None
            if got is None:
                no_extraction += 1
                print(f"{account_name[:24]:<24} {opportunity.qualification_score:>5} {'—':>5}  no extraction, left alone")
                continue

            scored, _signals = got
            before = opportunity.qualification_score

            # The driver, when one exists. None means nothing scoreable — a
            # real answer about the account, not a label to overwrite with;
            # the stored value (usually "other") stands.
            driver = scored.signal_type.value if scored.signal_type else None
            type_stale = driver is not None and driver != opportunity.signal_type

            if (
                scored.score == before
                and scored.needs_review == opportunity.needs_review
                and not type_stale
            ):
                unchanged += 1
                continue

            note = ""
            if before >= threshold > scored.score:
                dropped_below.append((account_name, before, scored.score))
                note = f"now below the {threshold} threshold"
            if type_stale:
                note = f"{note}  {opportunity.signal_type} -> {driver}".strip()

            changed += 1
            print(f"{account_name[:24]:<24} {before:>5} {scored.score:>5}  {note}")

            if not dry_run:
                opportunity.qualification_score = scored.score
                opportunity.needs_review = scored.needs_review
                # signal_type moves with the score: both come out of the same
                # scoring pass, and a fresh score beside a stale label is the
                # inconsistency this script exists to remove.
                if type_stale:
                    opportunity.signal_type = driver

        if not dry_run:
            await session.commit()

        print(
            f"\n{'would change' if dry_run else 'changed'}: {changed}   "
            f"already agreed: {unchanged}   "
            f"skipped (decided): {skipped_decided}   "
            f"no extraction: {no_extraction}"
        )
        if dropped_below:
            print(f"\ndropped below the {threshold} threshold — still visible, now ranked low:")
            for name, before, after in dropped_below:
                print(f"   {name}: {before} -> {after}")
        if skipped_decided:
            print(
                f"\n{skipped_decided} decided opportunit(ies) keep their original score "
                "and will still differ from their breakdown. That is intentional — see "
                "this script's docstring."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--include-decided",
        action="store_true",
        help="also rewrite accepted/rejected opportunities (rewrites the record)",
    )
    args = parser.parse_args()
    asyncio.run(rescore(dry_run=args.dry_run, include_decided=args.include_decided))


if __name__ == "__main__":
    main()
