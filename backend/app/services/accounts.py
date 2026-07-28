"""Sync accounts from the Rox customer hierarchy into our local store."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import Account, utcnow
from app.rox.client import RoxClient, first_present, flatten_hierarchy

log = get_logger(__name__)


async def sync_accounts(session: AsyncSession, rox: RoxClient) -> list[Account]:
    """Upsert every account in the Rox hierarchy. Returns all local accounts.

    Keyed on `rox_entity_id`, so re-running is safe and picks up renames.
    """
    payload = await rox.get_customer_hierarchy()
    nodes = flatten_hierarchy(payload)

    existing = {
        a.rox_entity_id: a
        for a in (await session.execute(select(Account))).scalars().all()
    }

    created = updated = 0
    for node in nodes:
        entity_id = node["_id"]
        account = existing.get(entity_id)
        if account is None:
            account = Account(rox_entity_id=entity_id, name=node["_name"])
            session.add(account)
            existing[entity_id] = account
            created += 1
        else:
            account.name = node["_name"]
            updated += 1

        account.domain = _as_str(first_present(node, "domain", "website", "url"))
        account.industry = _as_str(first_present(node, "industry", "sector", "vertical"))
        account.parent_rox_entity_id = node.get("_parent_id")
        account.raw = {k: v for k, v in node.items() if not k.startswith("_")}
        account.synced_at = utcnow()

    await session.commit()
    log.info("accounts synced", created=created, updated=updated, total=len(existing))

    return list((await session.execute(select(Account))).scalars().all())


def _as_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:255] or None
