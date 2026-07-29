"""Rewrite `Opportunity.qualification_score` — and optionally its prose — from
extracted signals.

Opportunities created before phase 5 hold a score from `parse_cell`. The detail
view now computes a factor breakdown from the extracted signals, and the two
disagree on 17 of 21 rows — one reads 80 in the header over factors summing to
71. A headline number its own visible breakdown cannot explain is worse than a
changed number, which is the same reasoning `backfill_truncated_research`
applies to its rescore.

After this, `qualification_score` *is* `score_breakdown.total`, so the pipeline
table and the detail view agree.

`--rewrite-prose` additionally rebuilds `rationale` and `title` by running the
same `write_brief` elaboration that `create_opportunity` would have run. Those
rows hold prose from `parse_cell`, whose `_markdown_signals` keeps only the
first line of each bullet block — so a nested cell (a parent bullet naming the
category, child bullets carrying the facts) is stored as its parent line alone:

    Growth / Expansion — public fiscal metrics indicate scale and recent revenue:
      - JPMorgan reported total net revenue of ~$182.447 billion...   <- dropped
      - Total global employees reported as 318,512...                 <- dropped

which is why 14 of 21 rationales end mid-sentence or on a dangling colon. The
signals are already extracted, so the fix needs no Rox traffic.

Deliberate choices:

* **Decided opportunities are skipped.** Someone accepted or rejected them on
  the number they were shown; rewriting it afterwards falsifies the record.
  They keep their original score and will still disagree with their breakdown —
  a smaller, older residue than rewriting history. `--include-decided` overrides
  this, which you want only if the original decisions are being discarded too.
  This holds harder for prose than for the score: the rationale is the text a
  reviewer actually read before deciding.
* **`needs_review` moves with the score**, because both come out of the same
  composition — leaving a stale flag beside a fresh score is the inconsistency
  this script exists to remove.
* **Nothing is deleted.** An opportunity rescored below
  `OPPORTUNITY_SCORE_THRESHOLD` still exists; the threshold gates creation, not
  continued existence. Those rows are reported so the drop is not silent.
* **Opportunities with no extraction are left alone**, not zeroed. No signals
  means "not extracted", which is not the same as "scored zero".
* **Prose is evaluated independently of the score.** A row whose score already
  agrees with its breakdown can still hold a truncated rationale, so the
  score-unchanged case must not short-circuit the prose pass.
* **A failed brief leaves the row untouched.** `write_brief` returns None on
  refusal, transport failure, or an unparseable response; writing a partial
  would replace bad prose with none at all. Failures are counted and named.

`--rewrite-prose` calls the Anthropic API once per candidate row — including
under `--dry-run`, which is the point: the generated text is what you are
reviewing, and it cannot be shown without generating it. `--dry-run` still
commits nothing.

    .venv/bin/python -m scripts.rescore_from_signals --dry-run
    .venv/bin/python -m scripts.rescore_from_signals
    .venv/bin/python -m scripts.rescore_from_signals --rewrite-prose --dry-run
    .venv/bin/python -m scripts.rescore_from_signals --rewrite-prose
"""

from __future__ import annotations

import argparse
import asyncio
import re

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Account, Opportunity, OpportunityResearchLink, OpportunityStatus
from app.signals.elaboration import write_brief
from app.signals.service import scored_for_artifact

#: Matches `create_opportunity`, which stores `f"{account}: {headline}"[:255]`.
TITLE_LIMIT = 255


def _preview(text: str | None, width: int = 96) -> str:
    """One-line rendering for the diff output.

    Newlines are collapsed rather than kept: the stored rationale carries a
    `\\n\\n` between the brief body and its why-now clause, and letting that
    break the table would hide the line the reader is comparing against.
    """
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat if len(flat) <= width else f"{flat[: width - 1]}…"


async def rescore(
    *,
    dry_run: bool,
    include_decided: bool,
    rewrite_prose: bool,
    only: set[str] | None = None,
) -> None:
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

        score_changed = prose_changed = unchanged = 0
        skipped_decided = no_extraction = 0
        dropped_below: list[tuple[str, int, int]] = []
        prose_failed: list[str] = []

        print(f"{'account':<24} {'from':>5} {'to':>5}  note")
        print("-" * 72)

        for opportunity, account_name in rows:
            if only is not None and account_name not in only:
                continue

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

            scored, signals = got
            before = opportunity.qualification_score
            score_differs = (
                scored.score != before or scored.needs_review != opportunity.needs_review
            )

            if score_differs:
                note = ""
                if before >= threshold > scored.score:
                    dropped_below.append((account_name, before, scored.score))
                    note = f"now below the {threshold} threshold"

                score_changed += 1
                print(f"{account_name[:24]:<24} {before:>5} {scored.score:>5}  {note}")

                if not dry_run:
                    opportunity.qualification_score = scored.score
                    opportunity.needs_review = scored.needs_review

            # Independent of the score: a row already agreeing with its
            # breakdown can still hold a rationale truncated by `parse_cell`.
            if not rewrite_prose:
                if not score_differs:
                    unchanged += 1
                continue

            brief = await write_brief(account_name, signals)
            if brief is None:
                prose_failed.append(account_name)
                print(f"{account_name[:24]:<24} {'':>5} {'':>5}  brief failed, prose left alone")
                if not score_differs:
                    unchanged += 1
                continue

            title = f"{account_name}: {brief.headline}"[:TITLE_LIMIT]
            rationale = f"{brief.rationale}\n\n{brief.why_now}".strip()
            if title == opportunity.title and rationale == opportunity.rationale:
                if not score_differs:
                    unchanged += 1
                continue

            prose_changed += 1
            if not score_differs:
                print(f"{account_name[:24]:<24} {before:>5} {'=':>5}  prose only")
            print(f"    - {_preview(opportunity.rationale)}")
            print(f"    + {_preview(rationale)}")

            if not dry_run:
                opportunity.title = title
                opportunity.rationale = rationale

        if not dry_run:
            await session.commit()

        verb = "would change" if dry_run else "changed"
        print(
            f"\n{verb} (score): {score_changed}   "
            f"{verb} (prose): {prose_changed}   "
            f"already agreed: {unchanged}   "
            f"skipped (decided): {skipped_decided}   "
            f"no extraction: {no_extraction}"
        )
        if dropped_below:
            print(f"\ndropped below the {threshold} threshold — still visible, now ranked low:")
            for name, before, after in dropped_below:
                print(f"   {name}: {before} -> {after}")
        if prose_failed:
            print(
                f"\n{len(prose_failed)} brief(s) failed and kept their existing prose: "
                f"{', '.join(prose_failed)}\nRe-running retries only these."
            )
        if skipped_decided:
            print(
                f"\n{skipped_decided} decided opportunit(ies) keep their original score "
                "and prose, and will still differ from their breakdown. That is "
                "intentional — see this script's docstring."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--include-decided",
        action="store_true",
        help="also rewrite accepted/rejected opportunities (rewrites the record)",
    )
    parser.add_argument(
        "--rewrite-prose",
        action="store_true",
        help="also rebuild rationale and title via write_brief (calls the Anthropic API)",
    )
    # `write_brief` is not deterministic, so --rewrite-prose reports every row as
    # changed even when only the wording moved. Without a way to target the rows
    # whose signals actually changed, a blanket run rewrites good prose to no
    # effect and bills a call for each. Scope it by account instead.
    parser.add_argument(
        "--account",
        action="append",
        metavar="NAME",
        help="limit to this account (repeatable); matches Account.name exactly",
    )
    args = parser.parse_args()
    asyncio.run(
        rescore(
            dry_run=args.dry_run,
            include_decided=args.include_decided,
            rewrite_prose=args.rewrite_prose,
            only=set(args.account) if args.account else None,
        )
    )


if __name__ == "__main__":
    main()
