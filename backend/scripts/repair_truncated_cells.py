"""Rewrite `cell_value` from `raw.output.text` where it was stored truncated.

`research.py` prefers `output.text` — the full narrative — and falls back to
Rox's ~300-char capped `cell_value` only when it is absent. Artifacts written
before that fix hold the capped copy, cut mid-value:

    cell_value       303 chars   {"signal":"Trigger Event","evidence":"Microsoft
                                  corporate blog (2026-07-...
    raw.output.text  2127 chars  the full narrative

Extraction then did exactly what it should with what it was given, which is why
those signals look mangled rather than wrong: the evidence span really does
stop there, and the containment check passed it because a prefix of a real
sentence is still a substring of the source.

The whole response was stored in `raw`, so the narrative never left the
database. 42 of 432 artifacts are repairable this way with no Rox call.

This supersedes `backfill_truncated_research` for those 42. That script fetches
fresh research because it assumes the original is unrecoverable — true for the
42 artifacts whose `raw` carries no `output.text` at all, and wrong for these.
Prefer this script first; it is local, exact, and restores the text the
decision was actually made on rather than whatever Rox returns today.

Deliberate choices:

* **Artifacts behind a decided opportunity are skipped.** The artifact is the
  evidence someone accepted or rejected on. Rewriting it afterwards changes
  what the record says they saw. `--include-decided` overrides, which you want
  only if those decisions are being discarded too.
* **Unlinked artifacts are repaired.** Most artifacts back no opportunity at
  all — the research cycle writes one per account every 15 minutes. Repairing
  them falsifies nothing and stops the next opportunity inheriting the cut.
* **Only linked artifacts are re-extracted.** Repairing a cell is a local
  column write and runs everywhere, because it stops the next opportunity
  inheriting the cut. Re-extraction costs an API call each and runs only where
  the result is read: 21 of 432 artifacts back an opportunity, and both signal
  read paths reach them through `OpportunityResearchLink`. Re-extracting all 39
  repairable artifacts to surface one account's signals is the wrong trade.
* **Scores are not rewritten here.** Re-extraction changes the signals, which
  makes `qualification_score` stale — but that is `rescore_from_signals`' job,
  and doing both in one script would make the score movement impossible to
  attribute. Affected opportunities are reported, not touched.
* **Repairs commit before re-extraction.** Extraction makes an API call per
  artifact. If it fails partway the repaired cells persist, so a re-run
  resumes instead of starting over.

    .venv/bin/python -m scripts.repair_truncated_cells --dry-run
    .venv/bin/python -m scripts.repair_truncated_cells
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    Account,
    Opportunity,
    OpportunityResearchLink,
    OpportunityStatus,
    ResearchArtifact,
)
from app.signals.extraction import content_hash, extract_artifact
from app.signals.models import ArtifactExtraction
from app.signals.service import scored_for_artifact


def full_text(artifact: ResearchArtifact) -> str | None:
    """The untruncated narrative sitting in `raw`, if it is longer than what
    was stored.

    Longer, not merely different: across all 432 artifacts `output.text` is
    either identical to `cell_value` (stored correctly) or longer (stored from
    the capped copy). It is never shorter, so length alone identifies the
    mis-stored rows without a heuristic about what truncation looks like.
    """
    output = (artifact.raw or {}).get("output")
    text = output.get("text") if isinstance(output, dict) else None
    if not isinstance(text, str):
        return None
    return text if len(text) > len(artifact.cell_value or "") else None


async def decided_artifact_ids(session) -> set[str]:
    """Artifacts backing an accepted or rejected opportunity."""
    rows = await session.execute(
        select(OpportunityResearchLink.artifact_id)
        .join(Opportunity, Opportunity.id == OpportunityResearchLink.opportunity_id)
        .where(Opportunity.status != OpportunityStatus.NEW.value)
    )
    return set(rows.scalars().all())


async def stale_linked_artifacts(session, protected: set[str]) -> list[tuple]:
    """Linked artifacts whose extraction no longer matches their cell text.

    Selection is by `content_hash` rather than by "did we just repair it",
    which is what makes this resumable. Repairs commit before any API call, so
    a run interrupted partway leaves cells fixed and extractions stale — and a
    repair-driven selection would then find nothing to do while the stale
    signals stayed. Comparing the stored hash against the current text catches
    those on the next run, and is a no-op once everything agrees.
    """
    settings = get_settings()
    rows = (
        await session.execute(
            select(ResearchArtifact, Account.name)
            .join(Account, ResearchArtifact.account_id == Account.id)
            .join(
                OpportunityResearchLink,
                OpportunityResearchLink.artifact_id == ResearchArtifact.id,
            )
        )
    ).all()

    stale: list[tuple] = []
    seen: set[str] = set()
    for artifact, account_name in rows:
        # An artifact can back more than one opportunity; extract it once.
        if artifact.id in seen or artifact.id in protected:
            continue
        seen.add(artifact.id)

        # The text it *will* hold — so a dry run reports what a real run does
        # without mutating anything to find out.
        effective = full_text(artifact) or artifact.cell_value or ""
        existing = (
            await session.execute(
                select(ArtifactExtraction).where(
                    ArtifactExtraction.artifact_id == artifact.id,
                    ArtifactExtraction.schema_version
                    == settings.extraction_schema_version,
                )
            )
        ).scalar_one_or_none()

        if existing is None or existing.content_hash != content_hash(effective):
            stale.append((artifact, account_name))

    return stale


async def repair(*, dry_run: bool, include_decided: bool) -> None:
    await init_db()

    async with SessionLocal() as session:
        protected = set() if include_decided else await decided_artifact_ids(session)

        rows = (
            await session.execute(
                select(ResearchArtifact, Account.name)
                .join(Account, ResearchArtifact.account_id == Account.id)
                .order_by(ResearchArtifact.fetched_at)
            )
        ).all()

        candidates: list[tuple[ResearchArtifact, str]] = []
        skipped_decided = 0

        print(f"{'account':<24} {'stored':>7} {'recovered':>10}  note")
        print("-" * 64)

        for artifact, account_name in rows:
            text = full_text(artifact)
            if text is None:
                continue
            if artifact.id in protected:
                skipped_decided += 1
                continue

            print(
                f"{account_name[:24]:<24} {len(artifact.cell_value or ''):>7} "
                f"{len(text):>10}  {artifact.fetched_at:%Y-%m-%d %H:%M}"
            )
            candidates.append((artifact, account_name))
            if not dry_run:
                artifact.cell_value = text

        # Re-extraction is the only expensive step, so it runs where the result
        # is read. Repairing the cell is a local column write and worth doing
        # everywhere — it stops a future opportunity inheriting the cut — but
        # signals on an unlinked artifact are dead data, and paying for 39
        # extraction calls to surface one account's is the wrong trade.
        to_extract = await stale_linked_artifacts(session, protected)
        # Reporting only: repairs and re-extractions are independent sets now,
        # so "repaired but nothing reads it" needs the full linked set, not the
        # stale subset.
        linked = set(
            (await session.execute(select(OpportunityResearchLink.artifact_id)))
            .scalars()
            .all()
        )
        repaired_unlinked = sum(1 for a, _ in candidates if a.id not in linked)

        if dry_run:
            print(
                f"\nwould repair: {len(candidates)}   "
                f"stale extractions to refresh (linked): {len(to_extract)}   "
                f"skipped (behind a decision): {skipped_decided}"
            )
            _explain(skipped_decided, repaired_unlinked)
            return

        # Commit the repairs before spending an API call on any of them.
        await session.commit()
        print(
            f"\nrepaired {len(candidates)} cell(s); "
            f"refreshing {len(to_extract)} stale extraction(s) that back an opportunity...\n"
        )

        outcomes: dict[str, int] = {}
        for artifact, account_name in to_extract:
            _record, outcome = await extract_artifact(
                session, artifact, account_name, force=True
            )
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            await session.commit()

        print("re-extraction: " + "   ".join(f"{k}: {v}" for k, v in sorted(outcomes.items())))
        await _report_stale_scores(session, to_extract)
        _explain(skipped_decided, repaired_unlinked)


async def _report_stale_scores(session, candidates) -> None:
    """Opportunities whose stored score no longer matches its own signals.

    Reported rather than corrected: re-extraction is this script's change, and
    rescoring on top of it would leave no way to tell which of the two moved a
    number.
    """
    seen: set[str] = set()
    stale: list[tuple[str, int, int]] = []

    for artifact, account_name in candidates:
        links = (
            await session.execute(
                select(OpportunityResearchLink.opportunity_id).where(
                    OpportunityResearchLink.artifact_id == artifact.id
                )
            )
        ).scalars().all()

        for opportunity_id in links:
            if opportunity_id in seen:
                continue
            seen.add(opportunity_id)

            opportunity = await session.get(Opportunity, opportunity_id)
            got = await scored_for_artifact(session, artifact.id)
            if opportunity is None or got is None:
                continue
            scored, _signals = got
            if scored.score != opportunity.qualification_score:
                stale.append((account_name, opportunity.qualification_score, scored.score))

    if not stale:
        return

    print("\nstored scores now disagree with their signals:")
    for name, before, after in stale:
        print(f"   {name}: {before} -> {after}")
    print("\nRun `python -m scripts.rescore_from_signals --dry-run` to apply these.")


def _explain(skipped_decided: int, repaired_unlinked: int) -> None:
    if skipped_decided:
        print(
            f"\n{skipped_decided} artifact(s) skipped because an accepted or rejected "
            "opportunity was decided on them. Repairing those would change what the "
            "record says the reviewer saw — see this script's docstring."
        )
    if repaired_unlinked:
        print(
            f"\n{repaired_unlinked} repaired artifact(s) back no opportunity and were "
            "not re-extracted. Any signals they hold came from the truncated text and "
            "are now stale. Nothing reads them — both signal read paths go through "
            "OpportunityResearchLink — but `extraction_health` still counts them."
        )
    print(
        "\nArtifacts whose `raw` carries no output.text at all are not touched here; "
        "only a Rox re-fetch recovers those."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--include-decided",
        action="store_true",
        help="also repair artifacts behind accepted/rejected opportunities",
    )
    args = parser.parse_args()
    asyncio.run(repair(dry_run=args.dry_run, include_decided=args.include_decided))


if __name__ == "__main__":
    main()
