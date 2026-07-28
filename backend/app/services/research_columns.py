"""Which Rox research column feeds the pipeline.

This registry is the seam for changing the signal. The column is authored in
the Rox UI (API-created columns never generate cells — see
`scripts/probe_column_generation.py`), and referenced here **by name** so the
same code works across orgs where the UUIDs differ.

To change what drives opportunities, edit `COLUMN_REFS` — or set
`RESEARCH_COLUMNS` in .env to a comma-separated list of Rox column names for a
quick experiment. Nothing else needs to change: `research.py` resolves and
fetches whatever is listed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class ColumnRef:
    key: str
    #: name to resolve in /account_research/account_research_section
    rox_name: str
    #: when True, the run fails loudly if the column can't be resolved
    required: bool = False
    description: str = ""


#: Verified populated in the demo org (2026-07-27).
COLUMN_REFS: list[ColumnRef] = [
    ColumnRef(
        key="opportunity_signal",
        rox_name="Opportunity-Signal Research",
        required=True,
        description="Buying-signal strength with evidence-cited rationale.",
    ),
]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")


def active_column_refs() -> list[ColumnRef]:
    """The registry, optionally overridden by RESEARCH_COLUMNS in .env.

    The override takes Rox column *names*. Names already in the registry keep
    their configuration; unknown names are added as plain (non-required)
    columns.
    """
    override = (get_settings().research_columns or "").strip()
    if not override:
        return COLUMN_REFS

    by_name = {c.rox_name.lower(): c for c in COLUMN_REFS}
    refs: list[ColumnRef] = []
    for raw in override.split(","):
        name = raw.strip()
        if not name:
            continue
        known = by_name.get(name.lower())
        refs.append(
            known
            if known
            else ColumnRef(
                key=_slug(name),
                rox_name=name,
                description="Added via RESEARCH_COLUMNS override.",
            )
        )
    return refs or COLUMN_REFS
