"""Database models and session management.

Contracts are stored as JSON columns rather than shredded into relational
tables. They are versioned Pydantic models that will iterate during the POC, and
`shared/` is their single source of truth — mirroring their fields into columns
would create a second definition to keep in sync, which CLAUDE.md forbids.

Local development falls back to SQLite when `DATABASE_URL` is unset, so the
blueprint pipeline can be exercised without Postgres running. Railway supplies a
Postgres URL in deployment.
"""

import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./interviewer.db"


class DatabaseNotConfigured(RuntimeError):
    """Raised when a deployed instance has no real database."""


def is_deployed() -> bool:
    """True when running on Railway rather than a developer machine."""
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )


def database_url() -> str:
    """Resolve the database URL, normalising Railway's Postgres scheme.

    Railway hands out `postgresql://`; SQLAlchemy's async engine needs the
    asyncpg driver named explicitly or it picks the sync one and fails at
    connect time.

    The SQLite fallback is a local-development convenience and must never apply
    in deployment. Railway does not share a Postgres service's DATABASE_URL with
    other services automatically — it has to be added as a reference variable.
    Forget it and the fallback would engage silently: health checks green,
    interviews running, every transcript written to a container filesystem that
    is wiped on the next deploy. Refusing to start is the only safe behaviour.
    """
    configured = os.environ.get("DATABASE_URL")
    if not configured and is_deployed():
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set on a deployed instance. Add a reference "
            "variable on the service: DATABASE_URL = ${{Postgres.DATABASE_URL}}. "
            "Refusing to start rather than silently writing interviews to an "
            "ephemeral SQLite file."
        )

    url = configured or DEFAULT_DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Base(DeclarativeBase):
    pass


class SpecStatus(StrEnum):
    """Where a job is in the clarification flow."""

    AWAITING_CLARIFICATION = "awaiting_clarification"
    READY = "ready"


class BlueprintStatus(StrEnum):
    """Blueprint generation runs as a BackgroundTask at CV upload time.

    The status column is how the employer panel reports progress without a
    queue — see CLAUDE.md on async jobs.
    """

    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class InterviewStatus(StrEnum):
    """Lifecycle of one interview.

    `scheduled` means credentials exist and the room is booked, but nothing is
    running — the bot is only spawned when the candidate actually joins, so an
    interview created a week early costs nothing until it happens.
    """

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class FeedbackStatus(StrEnum):
    """Where the post-interview scoring got to."""

    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class RecordingStatus(StrEnum):
    """What happened to the recording of one interview.

    Six states rather than a nullable path, because the panel has to say
    something different for each and a null tells it nothing. "Never recorded",
    "recorded but we could not get it", "you deleted it" and "it aged out" are
    four different sentences to show a reviewer, and collapsing them would put a
    player on screen for a file that is not there.
    """

    #: No recording was attempted. Every interview from before this existed.
    NOT_RECORDED = "not_recorded"
    #: Daily accepted the start. The file is not ours yet.
    RECORDING = "recording"
    #: Downloaded, verified, and playable from our own disk.
    STORED = "stored"
    #: It was meant to exist and does not. The reason is in `recording_error`.
    UNAVAILABLE = "unavailable"
    #: A person deleted it deliberately.
    DELETED = "deleted"
    #: The retention window ran out and the sweep removed it.
    EXPIRED = "expired"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Job(Base):
    """One role being hired for: the JD plus the EvaluationSpec derived from it."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    #: What the employer calls this opening, typed at creation. The spec also
    #: carries a `role_title`, but that only exists once the clarification chat
    #: has finished — a profile has to be identifiable in the list before then.
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Free-text tag, typically a business unit. Three "Business Analyst"
    #: profiles for different units are otherwise indistinguishable.
    business_unit: Mapped[str | None] = mapped_column(String(128), nullable=True)

    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jd_text: Mapped[str] = mapped_column(Text)

    #: Draft spec, refined by the clarification chat. Stored even while
    #: incomplete so a half-finished conversation is not lost.
    evaluation_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    spec_status: Mapped[str] = mapped_column(
        String(32), default=SpecStatus.AWAITING_CLARIFICATION
    )
    #: Bumped every time the spec is reopened and changed. A blueprint records
    #: the version it was built from, so the panel can show which candidates
    #: are planned against a spec that has since moved on.
    spec_version: Mapped[int] = mapped_column(default=1, server_default="1")

    clarification_turns: Mapped[list["ClarificationTurn"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ClarificationTurn.index"
    )
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ClarificationTurn(Base):
    """One message in the employer clarification conversation."""

    __tablename__ = "clarification_turns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column()
    role: Mapped[str] = mapped_column(String(16))  # "assistant" | "employer"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="clarification_turns")


class Candidate(Base):
    """One candidate for one job, with their generated interview blueprint."""

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cv_text: Mapped[str] = mapped_column(Text)

    blueprint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    blueprint_status: Mapped[str] = mapped_column(
        String(32), default=BlueprintStatus.PENDING
    )
    blueprint_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    blueprint_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The employer's conversation about improving the plan, as
    #: [{"role": "employer"|"assistant", "content": ...}].
    blueprint_refinements: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    #: Which `Job.spec_version` this plan was built from. When the spec moves
    #: ahead of this, the plan is stale: it is testing for something the
    #: employer has since changed their mind about.
    spec_version: Mapped[int] = mapped_column(default=1, server_default="1")

    job: Mapped[Job] = relationship(back_populates="candidates")
    interviews: Mapped[list["Interview"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class Interview(Base):
    """One scheduled or conducted interview with one candidate."""

    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    #: Credentials the candidate is given. Indexed because the join endpoint
    #: looks up by meeting id on every attempt.
    meeting_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    #: Stored as written rather than hashed, deliberately and narrowly: the
    #: employer has to be able to re-read it to send it to the candidate, and it
    #: is a single-use credential for one short-lived room that expires on its
    #: own. It is never logged. A real deployment should either show it once at
    #: creation or encrypt it at rest.
    password: Mapped[str] = mapped_column(String(64))

    daily_room_name: Mapped[str] = mapped_column(String(128))
    daily_room_url: Mapped[str] = mapped_column(String(512))
    room_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(
        String(32), default=InterviewStatus.SCHEDULED, index=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: IANA timezone reported by the candidate's browser when they joined.
    #: Kept on the record because it is the only trace of what time it was
    #: for them, which a transcript timestamped in UTC cannot tell you.
    candidate_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The auditable ground truth (CLAUDE.md). Persisted in full, as the
    #: `Transcript` contract.
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Per-section outcomes from the brain: coverage, claims, contradictions.
    section_outcomes: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    #: Scored once, at the end, from the complete transcript above.
    feedback_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: pending | generating | ready | failed. Its own column rather than a
    #: null check on the report, so "not scored yet" and "scoring failed" stay
    #: distinguishable — otherwise a failure looks like work still in progress
    #: and nobody ever retries it.
    feedback_status: Mapped[str] = mapped_column(
        String(32), default=FeedbackStatus.PENDING, server_default=FeedbackStatus.PENDING
    )
    feedback_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: When the stored report was produced. Shown next to the regenerate
    #: button: recomputing without knowing how old the current one is, or
    #: whether it predates something you have since read, is a blind click.
    feedback_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Latency, turn-taking, silence and brain-transition telemetry.
    #:
    #: These used to live only in `backend/sessions/*.json`, which is fine on a
    #: laptop and useless on Railway — a container filesystem is ephemeral, so
    #: every redeploy would erase exactly the measurements we deploy in order to
    #: collect. Diagnosing a bad interview needs the transitions and the
    #: latencies, not just the words.
    session_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    #: Consent is captured before the candidate can join (Milestone 5). Recorded
    #: here so the timestamp sits alongside the interview it applies to.
    consent_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- the recording ------------------------------------------------------
    #
    # Video and audio of the call, for a person to watch afterwards. Nothing
    # reads it but a human: it is never scored, never analysed, and never sent
    # to a model (CLAUDE.md keeps proctoring out of scope, and this does not
    # reopen it).
    #
    # Daily records into its own storage, so the file arrives in two steps: the
    # bot starts it, and a sweep downloads it once Daily has finished
    # compositing and then deletes Daily's copy. Every column below is written by
    # one of those two steps.

    recording_status: Mapped[str] = mapped_column(
        String(32),
        default=RecordingStatus.NOT_RECORDED,
        server_default=RecordingStatus.NOT_RECORDED,
        index=True,
    )
    #: Daily's id for the finished recording, filled in when the sweep finds it.
    #: Kept after the local copy exists so a later deletion can also remove
    #: anything still sitting at Daily.
    recording_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recording_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Seconds between the transcript's zero and the video's zero. Transcript
    #: turns carry `at_seconds` from when the bot process started; the recording
    #: begins a moment later, once the bot is actually in the room. Subtracting
    #: this is what lets a click on a transcript line seek the video. Measured by
    #: the bot rather than derived from Daily's timestamps, so it is good to
    #: about a second: enough to find a moment, not enough to quote from.
    recording_offset_seconds: Mapped[float | None] = mapped_column(nullable=True)
    #: Where the file sits, relative to `backend/sessions/`. Relative so the
    #: record survives the directory moving, and so nothing in the database is a
    #: usable absolute path into the host.
    recording_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recording_bytes: Mapped[int | None] = mapped_column(nullable=True)
    #: When retention removes it. Ten days from the interview, not from the last
    #: time somebody watched it: the promise made to the candidate is about how
    #: long it is kept, and a clock that resets on access is not that promise.
    recording_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    recording_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why there is no recording, in words a reviewer can read. The panel shows
    #: this instead of a player.
    recording_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate: Mapped[Candidate] = relationship(back_populates="interviews")


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url(), future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def create_all() -> None:
    """Create tables, then add any column a live table is missing.

    `create_all` only creates tables that do not exist — it never alters one that
    does. Adding a field to a model above would therefore work on a fresh
    developer database and fail on the deployed one, which is the worst possible
    split. So after creating, we add whatever is missing.

    This handles the only migration shape a POC needs: a new nullable column.
    Renames, type changes and backfills are not attempted — they need real
    migrations, which arrive with real data.
    """
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_missing_columns)


def _add_missing_columns(connection) -> None:
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if not column.nullable and column.server_default is None:
                # Existing rows would have nothing to put here, and NOT NULL
                # forbids leaving it empty. A server_default answers the
                # question; without one, guessing a value would write a
                # fabricated number into real interview records.
                raise RuntimeError(
                    f"{table.name}.{column.name} is new and NOT NULL with no "
                    "server_default; it needs a real migration, not an "
                    "automatic ALTER."
                )
            ddl = CreateColumn(column).compile(connection.engine)
            connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))


async def reset_engine() -> None:
    """Drop cached engine/sessionmaker so tests can point at their own database."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
