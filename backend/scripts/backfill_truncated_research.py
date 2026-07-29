"""Re-attach readable research to opportunities whose cell was stored truncated.

Runs before the `output.text` fix stored the ~300-char capped `cell_value`
instead of the full narrative. Those cells are cut mid-sentence — often
mid-JSON-object — so nothing can be parsed out of them and the affected
opportunities show no score breakdown at all.

The original full text is **not recoverable**: those artifacts came from the
bulk endpoint, whose payload never carried `output.text`, and Rox has
regenerated the cells many times since. So this fetches *current* research and
attaches it as a new artifact under its own run, rather than pretending to
restore what was there.

Consequences, deliberately:

* The score is recomputed from the new research, because a headline number the
  visible breakdown cannot explain is worse than a changed number.
* **Decided opportunities are skipped.** Someone accepted or rejected them on
  the evidence available at the time; rewriting that evidence afterwards would
  falsify the record.
* The old artifact row is kept and merely unlinked, so the original remains
  auditable.

    .venv/bin/python -m scripts.backfill_truncated_research --dry-run
    .venv/bin/python -m scripts.backfill_truncated_research
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    Account,
    Opportunity,
    OpportunityResearchLink,
    OpportunityStatus,
    ResearchArtifact,
    ResearchColumn,
    ResearchRun,
    RunStatus,
    utcnow,
)
from app.rox.client import RoxClient
from app.services.parsing import parse_cell, parse_signals


async def rescore(dry_run: bool) -> None:
    """Recompute scores from research already stored — no Rox calls.

    Run this after a parser change: the linked artifacts already hold the full
    narrative, so a score that no longer matches its own breakdown can be
    corrected without touching the network. Decided opportunities are skipped
    for the same reason as in the backfill.
    """
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Opportunity, Account, ResearchArtifact)
                .join(Account, Account.id == Opportunity.account_id)
                .join(
                    OpportunityResearchLink,
                    OpportunityResearchLink.opportunity_id == Opportunity.id,
                )
                .join(
                    ResearchArtifact,
                    ResearchArtifact.id == OpportunityResearchLink.artifact_id,
                )
                .where(Opportunity.status == OpportunityStatus.NEW.value)
            )
        ).all()

        changed = 0
        for opp, account, artifact in rows:
            parsed = parse_cell(artifact.cell_value)
            if parsed.score is None:
                continue

            # Compare every derived field, not just the score. A parser change
            # that only cleans up the rationale leaves the score identical, and
            # keying on the score alone silently skipped those rows.
            score_moved = parsed.score != opp.qualification_score
            text_moved = bool(parsed.rationale) and parsed.rationale != opp.rationale
            type_moved = bool(parsed.signal_type) and parsed.signal_type != opp.signal_type
            if not (score_moved or text_moved or type_moved):
                continue

            what = ", ".join(
                part
                for part, moved in (
                    (f"score {opp.qualification_score} -> {parsed.score}", score_moved),
                    ("rationale", text_moved),
                    ("signal_type", type_moved),
                )
                if moved
            )
            print(f"  rescore {account.name:<24} {what}")
            changed += 1
            if dry_run:
                continue

            opp.qualification_score = parsed.score
            if parsed.signal_type:
                opp.signal_type = parsed.signal_type
            if parsed.rationale:
                opp.rationale = parsed.rationale

        if not dry_run:
            await session.commit()
        print(f"\n{'would rescore' if dry_run else 'rescored'}: {changed}")


async def main(dry_run: bool) -> None:
    async with SessionLocal() as session:
        column = (
            await session.execute(
                select(ResearchColumn).where(ResearchColumn.key == "opportunity_signal")
            )
        ).scalar_one_or_none()
        if column is None or not column.rox_column_id:
            raise SystemExit("opportunity_signal column is not resolved — run a cycle first")

        rows = (
            await session.execute(
                select(Opportunity, Account, ResearchArtifact)
                .join(Account, Account.id == Opportunity.account_id)
                .join(
                    OpportunityResearchLink,
                    OpportunityResearchLink.opportunity_id == Opportunity.id,
                )
                .join(
                    ResearchArtifact,
                    ResearchArtifact.id == OpportunityResearchLink.artifact_id,
                )
            )
        ).all()

        stale = [
            (opp, account, artifact)
            for opp, account, artifact in rows
            if not parse_signals(artifact.cell_value or "")
        ]
        pending = [t for t in stale if t[0].status == OpportunityStatus.NEW.value]
        frozen = [t for t in stale if t[0].status != OpportunityStatus.NEW.value]

        print(f"stale: {len(stale)}  refreshable: {len(pending)}  frozen (decided): {len(frozen)}")
        for opp, account, _ in frozen:
            print(f"  frozen  {account.name} — decided as {opp.status}, evidence left intact")

        if not pending:
            print("nothing to do")
            return

        run = ResearchRun(
            trigger="backfill",
            status=RunStatus.RUNNING.value,
            accounts_scanned=len(pending),
        )
        if not dry_run:
            session.add(run)
            await session.flush()

        changed = 0
        async with RoxClient() as rox:
            for opp, account, old_artifact in pending:
                data = await rox.get_clever_column_cell(
                    column.rox_column_id, account.rox_entity_id
                )
                output = data.get("output") if isinstance(data, dict) else None
                text = (
                    (output.get("text") if isinstance(output, dict) else None)
                    or (data.get("cell_value") if isinstance(data, dict) else None)
                    or ""
                )
                parsed = parse_cell(text)
                signals = [s for s in parse_signals(text) if s.score is not None]

                if not signals:
                    print(f"  skip    {account.name} — current research is unparseable too")
                    continue

                print(
                    f"  refresh {account.name:<24} score {opp.qualification_score} -> "
                    f"{parsed.score}  ({len(signals)} signals, {len(text)} chars)"
                )
                changed += 1
                if dry_run:
                    continue

                artifact = ResearchArtifact(
                    run_id=run.id,
                    account_id=account.id,
                    column_id=column.id,
                    cell_value=text,
                    raw=data if isinstance(data, dict) else None,
                )
                session.add(artifact)
                await session.flush()

                # Point the opportunity at the new artifact. The old row stays
                # for audit; only the link moves.
                await session.execute(
                    delete(OpportunityResearchLink).where(
                        OpportunityResearchLink.opportunity_id == opp.id
                    )
                )
                session.add(
                    OpportunityResearchLink(opportunity_id=opp.id, artifact_id=artifact.id)
                )

                # Keep the headline consistent with the breakdown now shown.
                if parsed.score is not None:
                    opp.qualification_score = parsed.score
                if parsed.signal_type:
                    opp.signal_type = parsed.signal_type
                if parsed.rationale:
                    opp.rationale = parsed.rationale

                run.cells_fetched += 1
                run.artifacts_created += 1

        if not dry_run:
            run.status = RunStatus.SUCCEEDED.value
            run.finished_at = utcnow()
            await session.commit()

        print(f"\n{'would refresh' if dry_run else 'refreshed'}: {changed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="recompute scores from stored research only — no Rox calls",
    )
    args = parser.parse_args()
    asyncio.run((rescore if args.rescore else main)(args.dry_run))
