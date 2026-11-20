"""Database models and session management.

Contracts are stored as JSON columns rather than shredded into relational
tables. They are versioned Pydantic models that will iterate during the POC, and
`shared/` is their single source of truth — mirroring their fields into columns
would create a second definition to keep in sync, which CLAUDE.md forbids.

A SQLite file is used only when `APP_ENV=development` and `DATABASE_URL` is unset, so the
blueprint pipeline can be exercised without Postgres running. Any other setup needs
`DATABASE_URL` pointing at Postgres, and `APP_ENV=production` refuses SQLite outright.
"""

import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# Loads the repo-root .env into the environment before anything below reads
# DATABASE_URL or APP_ENV. Every entry point that reaches this module (the app, alembic,
# the scripts) gets the same configuration without having to import bot.config first.
import bot.config  # noqa: F401

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, String, Text, false, func, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./interviewer.db"


class DatabaseNotConfigured(RuntimeError):
    """Raised when a deployed instance has no real database."""


def app_env() -> str:
    """The environment this process runs in: `development` or `production`.

    Set explicitly, never inferred. An earlier version guessed from Railway's own
    variables, and a host that did not set them would have been treated as a developer's
    laptop, with the SQLite fallback that loses every transcript on the next deploy.
    """
    return os.environ.get("APP_ENV", "").strip().lower()


def is_deployed() -> bool:
    """True when this is a deployed instance (APP_ENV=production)."""
    return app_env() == "production"


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
    configured = os.environ.get("DATABASE_URL", "").strip()
    if not configured and app_env() != "development":
        # Fail closed. Anything other than an explicit development setup needs a real
        # database, so a forgotten variable cannot fall back to a file in the container.
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Deployments must set DATABASE_URL to Postgres. "
            "For local development only, set APP_ENV=development to use the SQLite file. "
            "Refusing to guess, because a guess here loses every transcript."
        )

    url = configured or DEFAULT_DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if is_deployed() and url.startswith("sqlite"):
        raise DatabaseNotConfigured(
            "APP_ENV=production refuses a SQLite database. Point DATABASE_URL at Postgres."
        )
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

    # At most one live session per candidate, enforced by the database. Declared here
    # so the migration and the model are one definition; revision 0002 creates it.
    __table_args__ = (
        Index(
            "uq_interviews_one_live_per_candidate",
            "candidate_id",
            unique=True,
            sqlite_where=text("status IN ('scheduled', 'in_progress')"),
            postgresql_where=text("status IN ('scheduled', 'in_progress')"),
        ),
    )

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

    #: Whether this interview is recorded. Chosen by the admin per interview when it
    #: is scheduled, defaulting to RECORDING_ENABLED. The bot records only when it is
    #: started with --recording, and the join page reads it for the consent wording.
    recording_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

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

    #: When the interview page stopped being the thing in front of the candidate,
    #: and for how long. One entry per absence: `{"at_seconds", "seconds",
    #: "kind"}`.
    #:
    #: **Its own column rather than a key inside `session_metrics`, because two
    #: different writers own them.** The bot writes `session_metrics` once, at
    #: the end, from its own process. These arrive from the candidate's browser
    #: during the call, so folding them into the same column would mean the
    #: bot's final write silently clobbered every one of them.
    #:
    #: A fact the browser reported, and nothing more. There is no score, no
    #: threshold and no percentage here, because a number would imply a model
    #: nobody has validated: absences are shown to a reviewer as a count and a
    #: duration, and the reviewer decides what they mean. It never reaches the
    #: brain, the prompt, the judge or the scorer.
    focus_events: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

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
    # Declared as Float explicitly: a bare float annotation maps to Double, which
    # alembic reports as drift against the Float the baseline migration created.
    recording_offset_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Where the file sits, relative to `backend/sessions/`. Relative so the
    #: record survives the directory moving, and so nothing in the database is a
    #: usable absolute path into the host.
    recording_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Which store holds the file at recording_path: "local" (this container's disk, the
    #: original behaviour) or "s3". Recorded per row, because rows written before S3
    #: existed must keep being read from disk.
    recording_store: Mapped[str | None] = mapped_column(String(16), nullable=True)
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


class AdminRevocation(Base):
    """An admin session signed out before its own expiry.

    Sessions are signed tokens, so the signature alone cannot say they were ended. A row
    says this token ID is dead until `expires_at` (unix seconds). After that the token is
    rejected by its own expiry, so the row can be pruned.
    """

    __tablename__ = "admin_revocations"

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


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
    """Make a fresh database match the models, or refuse a database that is not migrated.

    The schema is changed only by Alembic revisions (see alembic/versions/), never
    by the running app. The old runtime step, which added missing columns to live
    tables, could not express a rename, a backfill or a dropped index, and it
    changed production silently at startup.

    - **No tables:** a new database. Create them from the models, then record the
      head revision, so a later `alembic upgrade head` knows where it stands.
    - **Tables, but no version record:** a database made before migrations existed.
      Refused, because nothing knows what it contains. The runbook says how to adopt it.
    - **A version other than head:** refused. Run `alembic upgrade head` first.
    """
    from app.migrations import SchemaBehind, SchemaNotMigrated, head_revision

    head = head_revision()
    async with get_engine().begin() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        if not tables:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": head},
            )
            return

        if "alembic_version" not in tables:
            raise SchemaNotMigrated(
                "This database has tables but no migration record, so its schema is "
                "unknown. Adopt it deliberately: check its schema, then run "
                "`alembic stamp <baseline>` and `alembic upgrade head`. See "
                "docs/OPERATIONS.md, 'Adopting an existing database'."
            )
        current = list(
            (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars()
        )

    if current != [head]:
        raise SchemaBehind(
            f"The database is at migration {current or 'none'} but the code needs "
            f"{head}. Run `alembic upgrade head` (from backend/) before starting the app."
        )


async def reset_engine() -> None:
    """Drop cached engine/sessionmaker so tests can point at their own database."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
