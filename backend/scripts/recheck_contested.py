"""Recompute the `contested` flag on stored signals. No LLM calls.

`contested` is a deterministic cross-check — the model's `is_absence` against
its own evidence span — so when the check improves, stored rows can be brought
up to date without re-extracting anything.

Written after General Electric scored 100, the maximum, on a `Buying Intent`
signal whose evidence read "no supporting account-level emails, meetings, notes,
or deals found". The model failed to set `is_absence`, and the guard missed it
because the negation was followed by adjectives before reaching a noun it knew.

Rescore afterwards — `scripts.rescore_from_signals` — or the flags will be right
while the scores built on them stay wrong.

    .venv/bin/python -m scripts.recheck_contested --dry-run
    .venv/bin/python -m scripts.recheck_contested
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Account
from app.signals.extraction import contests_absence
from app.signals.models import ArtifactExtraction, ExtractedSignalRow
from app.signals.schema import SignalType


async def recheck(*, dry_run: bool) -> None:
    await init_db()

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ExtractedSignalRow, Account.name)
                .join(
                    ArtifactExtraction,
                    ExtractedSignalRow.extraction_id == ArtifactExtraction.id,
                )
                .join(Account, ArtifactExtraction.account_id == Account.id)
            )
        ).all()

        newly, cleared = [], []

        for row, account_name in rows:
            should = (
                not row.is_absence
                and row.signal_type != SignalType.OTHER.value
                and contests_absence(row.evidence or "")
            )
            if should == bool(row.contested):
                continue

            (newly if should else cleared).append((account_name, row))
            if not dry_run:
                row.contested = should

        if not dry_run:
            await session.commit()

        print(f"signals checked: {len(rows)}")
        print(f"\nnewly contested ({len(newly)}) — counted as real signals until now:")
        for name, row in newly:
            print(f"   {name[:22]:<22} {row.signal_type:<24} conf={row.confidence}")
            print(f"      {(row.evidence or '')[:96]}")
        print(f"\nno longer contested ({len(cleared)}):")
        for name, row in cleared:
            print(f"   {name[:22]:<22} {row.signal_type:<24} conf={row.confidence}")

        if newly or cleared:
            print("\nrun scripts.rescore_from_signals next — scores are stale until you do")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args()
    asyncio.run(recheck(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
