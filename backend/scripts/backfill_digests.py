"""Reconstruct Digest rows for batch notifications sent before digests existed.

`notify_batch` wrote one Notification row per opportunity with no grouping id;
the only trace that rows went out together is an identical `sent_at` — the
batch path stamps every row in a send with one timestamp. This groups sent
notifications by (sent_at, recipient, channel) and writes a Digest plus
memberships for each group.

What counts as what:

* **Clean** — two or more rows sharing an exact timestamp. Microsecond
  precision makes an accidental collision between separate sends effectively
  impossible, and `notification_batch_size` has always been > 1, so a
  multi-row group can only be a batch.
* **Single** — a lone row at its timestamp. `notify_opportunity` and
  `retry_notification` produce these; a batch never could. Not a digest, not
  ambiguous — skipped, and counted so the report shows they were considered.
* **Ambiguous** — a multi-row group already partially covered by an existing
  digest membership (a previous partial backfill, or overlap with rows the
  new send paths wrote). Nothing is guessed: reported and left alone.

Idempotent: groups whose members all already belong to a digest are skipped
as already-covered. Backfilled digests carry trigger="backfill" and the
group's sent_at, so they are distinguishable from live sends forever.

    .venv/bin/python -m scripts.backfill_digests --dry-run
    .venv/bin/python -m scripts.backfill_digests
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Digest, DigestOpportunity, Notification, NotificationStatus


async def backfill(*, dry_run: bool) -> None:
    await init_db()

    async with SessionLocal() as session:
        sent = (
            await session.execute(
                select(Notification)
                .where(
                    Notification.status == NotificationStatus.SENT.value,
                    Notification.sent_at.is_not(None),
                )
                .order_by(Notification.sent_at)
            )
        ).scalars().all()

        already = set(
            (await session.execute(select(DigestOpportunity.opportunity_id))).scalars().all()
        )

        groups: dict[tuple, list[Notification]] = defaultdict(list)
        for row in sent:
            groups[(row.sent_at, row.recipient, row.channel)].append(row)

        clean = singles = ambiguous = covered = 0

        for (sent_at, recipient, channel), rows in sorted(groups.items()):
            if len(rows) == 1:
                singles += 1
                continue

            members = {r.opportunity_id for r in rows}
            overlap = members & already
            if overlap == members:
                covered += 1
                continue
            if overlap:
                ambiguous += 1
                print(
                    f"ambiguous: {sent_at} — {len(overlap)} of {len(rows)} rows already "
                    "belong to a digest; left alone"
                )
                continue

            clean += 1
            print(f"digest: {sent_at}  {len(rows)} opportunities  -> {recipient}")
            if dry_run:
                continue

            digest = Digest(
                channel=channel,
                recipient=recipient,
                trigger="backfill",
                status=NotificationStatus.SENT.value,
                sent_at=sent_at,
            )
            session.add(digest)
            await session.flush()
            for row in rows:
                session.add(
                    DigestOpportunity(
                        digest_id=digest.id, opportunity_id=row.opportunity_id
                    )
                )
            already.update(members)

        if not dry_run:
            await session.commit()

        verb = "would create" if dry_run else "created"
        print(
            f"\n{verb}: {clean} digest(s)   "
            f"single sends (not digests): {singles}   "
            f"already covered: {covered}   "
            f"ambiguous: {ambiguous}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
