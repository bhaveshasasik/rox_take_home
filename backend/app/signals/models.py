"""Persistence for extracted signals.

Two tables on the existing `Base`, both hanging off `research_artifacts`. The
artifact keeps the raw cell text permanently — nothing here overwrites or
prunes it — so re-extraction under a new `schema_version` is always available
as the escape hatch.

Registered for `create_all` by an import at the bottom of `app/models.py`,
which covers `db.init_db` and the test fixture in one place.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import UtcDateTime, utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


class ExtractionStatus(str, enum.Enum):
    OK = "ok"
    #: the model returned nothing scoreable for this cell — a real outcome,
    #: distinct from a failure, and not something to retry
    EMPTY = "empty"
    #: the call errored or came back malformed twice. Recorded with the error
    #: rather than dropped: a research format change shows up here as a spike
    #: long before anyone notices the queue has gone quiet.
    FAILED = "failed"


class ArtifactExtraction(Base):
    """One extraction pass over one artifact, at one schema version."""

    __tablename__ = "artifact_extractions"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id", "schema_version", name="uq_extraction_artifact_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("research_artifacts.id"), index=True
    )
    #: Denormalised from the artifact so the per-account brief can load a
    #: account's signals without joining back through artifacts and runs.
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)

    #: sha256 of the cell text this was extracted from. The research cycle
    #: writes a *new* artifact row for the same account every 15 minutes even
    #: when the text is byte-identical, so keying the cache on content rather
    #: than artifact id is what stops us re-paying for every unchanged cell.
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=ExtractionStatus.OK.value, index=True
    )
    #: the model that produced this, so a prompt/model change is attributable
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: every source available on the artifact, whether or not a signal cited it
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    signal_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)

    signals: Mapped[list[ExtractedSignalRow]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )


class ExtractedSignalRow(Base):
    """One signal from one cell."""

    __tablename__ = "extracted_signals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_extractions.id"), index=True
    )
    #: fixed vocabulary — see `schema.SignalType`. Stored as a plain string to
    #: match the rest of the schema's enum handling and keep SQLite readable.
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    #: 0-10 as emitted; the 0-100 composite is computed in `scoring.py`
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    is_absence: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: Evidence was not a verbatim span of the source cell after a retry, so it
    #: was discarded rather than kept. A span that cannot be located in the
    #: source is not evidence — it is generated text wearing evidence's clothes,
    #: and it is the one field the whole design promises is checkable.
    validation_failed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: `is_absence` is false but the evidence span asserts nothing was found.
    #: Recorded rather than corrected: the model's output is preserved as it
    #: came back, and scoring refuses to build on a claim that contradicts its
    #: own evidence. Observed on real cells — see `extraction.contests_absence`.
    contested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    #: verbatim span from the cell
    evidence: Mapped[str] = mapped_column(Text, default="")
    #: `[{"url": ..., "kind": "web"|"rox"}]`, resolved in code and validated
    #: against the artifact's known URLs — never taken from model output
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: order the model listed them in, preserved so evidence reads coherently
    position: Mapped[int] = mapped_column(Integer, default=0)

    extraction: Mapped[ArtifactExtraction] = relationship(back_populates="signals")
