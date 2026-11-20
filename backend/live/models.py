"""The stored event stream: one row per InterviewEvent.

On the shared `Base` (docs/DEV_PLAN.md section 6: each track's tables in its
own models.py). `seq` is the relay's cursor and the SSE event id; `event_id`
makes writes idempotent, so a batch retried after a lost acknowledgement never
stores an event twice.

Merge fixes recorded in track_i_notes/C_findings.md: an Alembic revision for
this table, and `org_id` becoming required with a foreign key to
`organisations` once Track H's tables exist.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InterviewEventRecord(Base):
    __tablename__ = "interview_events"

    #: Assigned at insert. Writers are serialised (live/store.py), so a row with
    #: a higher seq is never visible before one with a lower seq: the relay can
    #: resume from "after seq N" without skipping anything.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    #: Nullable until Track H's organisations table lands (merge fix).
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE")
    )
    at_seconds: Mapped[float] = mapped_column(Float)
    wall_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    #: The whole validated InterviewEvent, as JSON. The columns above are copies
    #: for querying; this is what the relay sends.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_interview_events_interview_seq", "interview_id", "seq"),)


class WatchRecord(Base):
    """One watcher token issued for one interview (live view, decision 7).

    The candidate consented to live watching; this is the record of who did.
    Written when the token is issued, before it is returned. The token itself
    is never stored. Merge fixes (C_findings.md): an Alembic revision, and
    `org_id` required with a foreign key, as for the other new tables.
    """

    __tablename__ = "watch_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    #: Who asked to watch. "admin" today (one shared admin); a user id after the
    #: merge (Track H's current_user()).
    watcher: Mapped[str] = mapped_column(String(64))
    #: The Daily user_id on the token ("watcher:<who>").
    daily_user_id: Mapped[str] = mapped_column(String(36))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecordingViewRecord(Base):
    """One request for an interview's recording file: who, which interview, when.

    Every request is a row, seeks included (a browser asks for byte ranges as
    the reviewer moves through the video), so "who watched this candidate" is
    answerable from the table alone. Written before the bytes are sent. Merge
    fixes (C_findings.md): an Alembic revision, `org_id` required with a
    foreign key, and `viewer` becoming Track H's user id.
    """

    __tablename__ = "recording_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    #: Who asked. "admin" today (one shared admin); a user id after the merge.
    viewer: Mapped[str] = mapped_column(String(64))
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    #: The Range header as sent ("bytes=0-"), or None for the whole file.
    byte_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Which route served it: "console" (signed in) or "panel" (the old /admin page).
    route: Mapped[str] = mapped_column(String(16))


class WatcherNoteRecord(Base):
    """One watcher's note (wave 1). The row is the audit: who wrote it and when.
    Never read by the bot; shown on the report apart from the evidence."""

    __tablename__ = "watcher_notes"

    note_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    #: "watcher:admin" today; a user id after the merge (Track H's current_user()).
    author: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(2000))
    #: Interview clock when it was written, stamped by the server (live/clock.py).
    at_seconds: Mapped[float] = mapped_column(Float)
    #: True when the clock was an estimate (no transcript zero yet).
    at_estimate: Mapped[bool] = mapped_column(default=False)
    #: The area current at that moment, from the stored area_changed events.
    area_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectionSignalRecord(Base):
    """What the web process noticed about the candidate's connection (wave 1):
    status readings stopping ("dropped") and starting again ("resumed"). The
    bot reads these (live/drops.py `signals_after`) and decides grace, counting
    and the ending; the server never does."""

    __tablename__ = "connection_signals"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))           # "dropped" | "resumed"
    detected_by: Mapped[str] = mapped_column(String(32))    # "status_readings"
    #: Interview clock: for "dropped" the last reading before the gap; for
    #: "resumed" the first reading after it.
    at_seconds: Mapped[float] = mapped_column(Float)
    #: For "resumed": seconds between the last reading before and the first after.
    gap_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RejoinPassRecord(Base):
    """One rejoin pass (wave 1): issued at join and at each exchange, used once.
    The pass itself is never stored, only its id; it is never logged."""

    __tablename__ = "rejoin_passes"

    pass_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapacityEventRecord(Base):
    """Phase 4 data (D-41.2): every join refused for being full, and the live
    bot count at every bot start, over time. Peak load is the largest
    `live_bots` in a window. Nothing personal: an interview id at most."""

    __tablename__ = "capacity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    kind: Mapped[str] = mapped_column(String(16))         # "refused" | "started"
    interview_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    live_bots: Mapped[int] = mapped_column(Integer)       # live bots at that moment (after a start)
    limit: Mapped[int] = mapped_column(Integer)           # MAX_CONCURRENT_INTERVIEWS then
