"""Stored integrity events and the candidate's accommodation.

On the shared `Base`. Merge fixes (track_i_notes/C_findings.md): Alembic
revisions for both tables, and `org_id` becoming required with a foreign key to
`organisations`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IntegrityEventRecord(Base):
    """One `IntegrityEvent` (shared/interview_contracts.py), as received."""

    __tablename__ = "integrity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    #: The browser's id. Unique per interview, which is what makes a resent
    #: batch harmless.
    event_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The whole validated IntegrityEvent, as JSON.
    event: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("interview_id", "event_id", name="uq_integrity_events_interview_event"),
    )


class IntegrityAccommodationRecord(Base):
    """What the candidate chose at consent (e.g. gaze reminders off). First value wins."""

    __tablename__ = "integrity_accommodations"

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gaze_reminders_off: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrityReportRecord(Base):
    """The STORED integrity report (`StoredIntegrityReport`), one per interview (Session D, D-93).

    Written when the interview ends, and rewritten when late events or after-call
    results arrive (proctoring/stored.py). Merge fix: an Alembic revision and org_id."""

    __tablename__ = "integrity_reports"

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
