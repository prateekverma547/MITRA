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
    #: Phase 4 data (D-41.4): booked, nobody joined, and the room expired. Written
    #: by the recording sweep's loop; before it, such interviews stayed
    #: "scheduled" forever. `expired` keeps its meaning: someone came too late.
    NO_SHOW = "no_show"


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

    # The organisation platform (docs/DEV_PLAN.md). Nullable because jobs made
    # in the old /admin panel have no organisation, BU or creator; scoping
    # treats them as the organisation's, visible to HR and admins only.
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    bu_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_units.id"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    #: draft (the BU head is still describing it), open (approved, HR can see
    #: it), closed. Old /admin jobs were usable at once, so they read as open.
    request_status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open"
    )
    #: What the clarification chat assumed but the employer never said, from
    #: the turn that finished the spec. Stored because "what MITRA understood"
    #: must show these beside the spec every time it is opened, not only in
    #: the reply that happened to carry them.
    spec_inferred: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

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
    #: The plan's text-model costs (C-A14): one `UsageRecord.to_dict()` per call
    #: (job, model, tokens, latency, cost_usd, price_known, price_source,
    #: attempts, outcome), for generation and for refinement with HR findings,
    #: failed calls included. Null on candidates from before. Phase 4 reads it.
    text_model_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
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

    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    #: Where they stand in the hiring flow: `shared.stages.CandidateStage`.
    #: Only `hiring.stages.move()` changes it, and every change leaves a row in
    #: `decisions`.
    stage: Mapped[str] = mapped_column(
        String(32), default="new", server_default="new", index=True
    )

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
    # Video and audio of the call, for a person to watch afterwards. This
    # combined recording is only ever watched by a person: it is never scored
    # and never analysed. Separately, when the after-call checks are switched on
    # (AFTERCALL_ENABLED, OFF by default until the consent wording covers it),
    # the bot also saves the candidate's own audio track, and a local
    # speaker-embedding model (backend/aftercall, no hosted service) looks for a
    # second voice in it. The result is a signal for a person to review in the
    # Integrity section, never part of a score. Both are kept 10 days.
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
    #: Seconds between the transcript's zero and the video's zero:
    #: file_second = at_seconds - recording_offset_seconds. The recording
    #: begins once the bot is in the room and Daily acknowledges the start, so
    #: the bot measures this as (acknowledgement time - transcript zero). Daily's
    #: own `start_ts` is NOT used: it is whole seconds and was 2.3 s early.
    #: Measured: a sound lands in the file 0.08 to 0.09 s LATER than
    #: at_seconds - offset predicts (D-05: about 0.08 s over five beeps; C-90:
    #: +0.085 to +0.089 s over five beeps, a second room and run). Stored to
    #: 0.01 s. Good enough to seek to a moment and to line up a quote; the
    #: sweep never changes it.
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
    #: Downloads from Daily that failed in a row (the sweep's attempts, C-103).
    #: Reset to 0 when one succeeds. Null on rows from before it existed (= 0).
    #: At `recordings.DOWNLOAD_FAILING_AFTER` the reason goes in
    #: `recording_error` and the console shows "download_failing"; the status
    #: stays "recording" because Daily still has the file and it is retried.
    recording_download_failures: Mapped[int | None] = mapped_column(nullable=True)
    #: The bot's process id (wave 1, D-43 option A): written when it is spawned,
    #: so a restarted web process can tell a live bot (adopted) from a dead one
    #: (the interview is ended as interrupted). Null on rows from before.
    bot_pid: Mapped[int | None] = mapped_column(nullable=True)
    #: The sweep collecting this recording right now, and until when (C-125): a
    #: second sweep at the same time sees the claim and skips, so the file is
    #: downloaded once. Null when nobody is collecting; expires by itself if
    #: its holder dies.
    recording_collect_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recording_collect_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The duration booked for this interview (task 20): what HR chose and
    #: sees, 20-60 minutes. Null for interviews booked before it existed.
    booked_duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    #: What the bot must run to (live/booking.py): on the realtime engine the
    #: planned minutes and the grace are trimmed so the hard stop lands by 58
    #: minutes of session time. The bot does not read these yet: it still takes
    #: the plan's duration from the blueprint (joint A + C).
    planned_duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    planned_grace_minutes: Mapped[int | None] = mapped_column(nullable=True)
    #: UTC wall time of the bot's transcript zero: every TranscriptTurn.at_seconds
    #: counts from it. The server's interview clock reads it (live/clock.py).
    #: Written by the bot (joint A + C step); null until then.
    transcript_zero_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The cleaned transcript (C-A6), a `CleanedTranscript` as JSON
    #: (shared/interview_contracts.py), written by the bot after the call beside
    #: the raw `transcript`, which is never changed. Null for interviews before
    #: it existed or when cleaning has not run: readers use the raw transcript.
    cleaned_transcript: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    #: Which round this room is for. `technical` is MITRA's interview (the bot
    #: joins). `hr` is a person's interview: HR and the candidate only, and the
    #: join path never starts a bot for it. Every interview before the
    #: organisation platform was technical, hence the default.
    round: Mapped[str] = mapped_column(String(16), default="technical", server_default="technical")

    candidate: Mapped[Candidate] = relationship(back_populates="interviews")


#: Packages that define tables on this `Base` outside this file. Each track
#: keeps its tables in its own `models.py` (docs/DEV_PLAN.md section 6), so
#: they are listed here once rather than imported ad hoc: anything that needs
#: the whole schema (migrations, `create_all`) calls `load_models()` first.
MODEL_MODULES: tuple[str, ...] = ("org.models", "hiring.models", "live.models", "proctoring.models")


def load_models() -> None:
    import importlib

    for name in MODEL_MODULES:
        importlib.import_module(name)


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
    load_models()
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_missing_columns)


#: The revision that describes the schema as it was before migrations existed.
BASELINE_REVISION = "0001"


async def prepare_schema() -> None:
    """Bring the database to the latest migration. Run at startup.

    Three cases:
    - a fresh database: every migration runs, from the baseline up;
    - a database `create_all` built before migrations existed (stg,
      production): stamped at the baseline, which matches what `create_all`
      made, then upgraded from there;
    - an already-migrated database: whatever is new runs.
    """
    load_models()
    async with get_engine().begin() as connection:
        await connection.run_sync(_upgrade_to_head)


def _upgrade_to_head(connection) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" not in tables and "jobs" in tables:
        # Built by `create_all`, never migrated. If it already holds every
        # table this code defines, `create_all` made it from today's models
        # (Track I's tests and scripts still do): it is at head. Otherwise it
        # is the schema stg was left with before migrations: the baseline.
        complete = set(Base.metadata.tables) <= tables
        command.stamp(config, "head" if complete else BASELINE_REVISION)
        if complete:
            # As `create_all` itself would: a nullable column added since is filled in.
            _add_missing_columns(connection)
    command.upgrade(config, "head")


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


# Register every track's tables on `Base` as soon as this module is imported,
# so a foreign key to `organisations` or `users` always has its table. The
# modules import `Base` from here, which already exists at this point.
load_models()
