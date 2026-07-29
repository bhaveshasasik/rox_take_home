"""ORM models for the Opportunity -> Qualified pipeline.

Stage flow:
    researched -> opportunity_created -> notified -> accepted | rejected
               -> prospected -> sequenced -> outreach_sent
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """Stores naive UTC, hands back timezone-aware UTC.

    SQLite has no timezone-aware type. Plain `DateTime` silently drops tzinfo
    on write and returns a naive value on read, which FastAPI then serialises
    with no offset at all — `2026-07-28T06:26:29.486897`. Every JavaScript
    client parses an offset-less datetime as *local* time, so a browser at
    UTC-7 reads that as 13:26 UTC and every rendered age is off by the
    viewer's offset. It looks correct in UTC, which is how it survives review.

    Storage format is unchanged (naive UTC), so rows written before this type
    existed are read back correctly and no migration is needed.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # Naive input is taken as UTC — that is what every writer here means
            # and what rows predating this type contain.
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Enums (stored as plain strings so SQLite stays readable / migration-free)
# --------------------------------------------------------------------------


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class OpportunityStatus(str, enum.Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Stage(str, enum.Enum):
    RESEARCHED = "researched"
    OPPORTUNITY_CREATED = "opportunity_created"
    NOTIFIED = "notified"
    #: Reporting-only: an opportunity is never *stored* at this stage, but the
    #: funnel needs "a human actually decided" as a step. `notified` only means
    #: the reviewer was emailed, which overstates review throughput.
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROSPECTED = "prospected"
    SEQUENCED = "sequenced"
    OUTREACH_SENT = "outreach_sent"


class DecisionType(str, enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"


class ReasonCode(str, enum.Enum):
    GOOD_FIT = "good_fit"
    BAD_TIMING = "bad_timing"
    WRONG_PERSONA = "wrong_persona"
    ALREADY_ENGAGED = "already_engaged"
    LOW_SIGNAL = "low_signal"
    OTHER = "other"


class Channel(str, enum.Enum):
    EMAIL = "email"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class SequenceStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class EnrollmentStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REPLIED = "replied"
    BOUNCED = "bounced"
    COMPLETED = "completed"


class EmailStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENT = "sent"
    OPENED = "opened"
    REPLIED = "replied"


# --------------------------------------------------------------------------
# Accounts & research
# --------------------------------------------------------------------------


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    rox_entity_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_rox_entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="account")


class ResearchColumn(Base):
    """A Rox clever_column the pipeline reads.

    Columns are authored in the Rox UI (API-created ones never generate
    cells), so we hold a reference rather than a definition.
    """

    __tablename__ = "research_columns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    rox_column_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rox_section_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    trigger: Mapped[str] = mapped_column(String(32), default="scheduled")
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.RUNNING.value)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    accounts_scanned: Mapped[int] = mapped_column(Integer, default=0)
    columns_refreshed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_enqueued: Mapped[int] = mapped_column(Integer, default=0)
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)
    cells_fetched: Mapped[int] = mapped_column(Integer, default=0)
    #: refreshes that were accepted but scheduled no cell generation
    cells_timed_out: Mapped[int] = mapped_column(Integer, default=0)
    #: cells that were fetched but yielded no score, so produced no opportunity.
    #: Counted because it is otherwise invisible: a research format Rox changes
    #: under us shows up here as a spike long before anyone notices the queue
    #: has gone quiet.
    cells_unscoreable: Mapped[int] = mapped_column(Integer, default=0)
    artifacts_created: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifacts: Mapped[list[ResearchArtifact]] = relationship(back_populates="run")

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class ResearchArtifact(Base):
    """One clever_column cell value for one account, captured during one run."""

    __tablename__ = "research_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "account_id", "column_id", name="uq_artifact_cell"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    column_id: Mapped[str] = mapped_column(ForeignKey("research_columns.id"))
    cell_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    run: Mapped[ResearchRun] = relationship(back_populates="artifacts")
    account: Mapped[Account] = relationship()
    column: Mapped[ResearchColumn] = relationship()


class OpportunityResearchLink(Base):
    """Supporting research attached to an opportunity."""

    __tablename__ = "opportunity_research_links"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "artifact_id", name="uq_opp_artifact"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(ForeignKey("research_artifacts.id"))

    artifact: Mapped[ResearchArtifact] = relationship()


# --------------------------------------------------------------------------
# Opportunities & decisions
# --------------------------------------------------------------------------


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.id"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255))
    rationale: Mapped[str] = mapped_column(Text)
    qualification_score: Mapped[int] = mapped_column(Integer, index=True)
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    #: set when the clever_column output could not be parsed into a clean
    #: score/signal and a human should sanity-check it
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(
        String(32), default=OpportunityStatus.NEW.value, index=True
    )
    stage: Mapped[str] = mapped_column(
        String(32), default=Stage.OPPORTUNITY_CREATED.value, index=True
    )
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    notified_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    account: Mapped[Account] = relationship(back_populates="opportunities")
    research_links: Mapped[list[OpportunityResearchLink]] = relationship(
        cascade="all, delete-orphan"
    )
    decision: Mapped[Decision | None] = relationship(
        back_populates="opportunity", uselist=False
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="opportunity"
    )
    contacts: Mapped[list[Contact]] = relationship(back_populates="opportunity")
    sequence: Mapped[Sequence | None] = relationship(
        back_populates="opportunity", uselist=False
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id"), unique=True, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), index=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: notified_at -> decided_at, powers the time-to-decision report
    latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="decision")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=NotificationStatus.PENDING.value, index=True
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="notifications")


# --------------------------------------------------------------------------
# Prospecting
# --------------------------------------------------------------------------


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id"), index=True
    )
    rox_contact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    persona: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: why prospecting surfaced this person for this opportunity's signal
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    opportunity: Mapped[Opportunity] = relationship(back_populates="contacts")
    account: Mapped[Account] = relationship()


class Sequence(Base):
    __tablename__ = "sequences"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id"), unique=True, index=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32), default=SequenceStatus.DRAFT.value, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    opportunity: Mapped[Opportunity] = relationship(back_populates="sequence")
    enrollments: Mapped[list[SequenceEnrollment]] = relationship(
        back_populates="sequence", cascade="all, delete-orphan"
    )


class SequenceEnrollment(Base):
    __tablename__ = "sequence_enrollments"
    __table_args__ = (
        UniqueConstraint("sequence_id", "contact_id", name="uq_enrollment"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("sequences.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), index=True)
    rox_enrollment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=EnrollmentStatus.PENDING.value, index=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    sequence: Mapped[Sequence] = relationship(back_populates="enrollments")
    contact: Mapped[Contact] = relationship()
    emails: Mapped[list[OutreachEmail]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan"
    )


class OutreachEmail(Base):
    __tablename__ = "outreach_emails"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    enrollment_id: Mapped[str] = mapped_column(
        ForeignKey("sequence_enrollments.id"), index=True
    )
    step_number: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default=EmailStatus.DRAFT.value, index=True
    )
    send_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    enrollment: Mapped[SequenceEnrollment] = relationship(back_populates="emails")


# Structured-signal tables live in `app/signals/models.py`. Imported here, at
# the bottom, so anything that imports `app.models` registers them too —
# `db.init_db` and the test fixture both call `create_all` off the back of
# this module, and a table nobody imported is a table `create_all` skips.
# Bottom placement is required: `signals.models` imports `UtcDateTime` from
# here, so the names must already be bound when it runs.
from app.signals import models as _signal_models  # noqa: E402,F401
