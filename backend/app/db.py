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

    #: The auditable ground truth (CLAUDE.md). Persisted in full, as the
    #: `Transcript` contract.
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Per-section outcomes from the brain: coverage, claims, contradictions.
    section_outcomes: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    #: Populated by Milestone 4.
    feedback_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

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
