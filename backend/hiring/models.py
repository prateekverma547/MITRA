"""The hiring record: every move of every candidate, and an audit trail.

`decisions` is the history behind `Candidate.stage`. The stage column says where
someone is now; this table says how they got there, who moved them, what the
model recommended at that moment, and why. Rows are only ever added.

`audit_events` records the other things people do that someone may later need
to account for: signing in, approving a job request, opening a transcript.

Track H owns this file.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    from_stage: Mapped[str] = mapped_column(String(32))
    to_stage: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    subject_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Screening(Base):
    """CV screening for one candidate: the fit analysis and the HR questions.

    One row per candidate, replaced on a re-run. It lives here rather than as
    columns on `candidates` so Track H's screening never edits the shared table.

    status: `waiting` (CV uploaded, HR has not chosen Proceed), `running`,
    `ready`, `failed`. Screening starts on Proceed rather than at upload,
    because the flow asks HR to confirm the CV was read correctly first, and
    screening a CV they are about to replace would be wasted work.
    """

    __tablename__ = "screenings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: shared.screening.FitAnalysis, as JSON.
    fit_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: shared.screening.HRQuestionSet, as JSON.
    hr_questions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HRRound(Base):
    """HR's working record of one HR round: what was asked and what they noted.

    The room, credentials and status live on the shared `interviews` row
    (`round = "hr"`); this holds what only Track H needs, so the HR round never
    adds columns to that table. The transcript and evaluation arrive in the
    next step of phase 2.
    """

    __tablename__ = "hr_rounds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), unique=True, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    booked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    #: HRQuestion ids HR ticked as asked, in the order they ticked them.
    questions_asked: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    hr_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Where the after-call work stands: not_started (round still open),
    #: waiting_recording, transcribing, evaluating, ready, failed.
    eval_status: Mapped[str] = mapped_column(String(24), default="not_started", server_default="not_started")
    eval_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The raw transcript, one entry per turn: speaker (hr, candidate, other),
    #: text, start, end. Never edited after it is written.
    transcript: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: How the voices were told apart, and how sure that is (decision 27).
    attribution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: shared.screening.HRRoundEvaluation, as JSON.
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
