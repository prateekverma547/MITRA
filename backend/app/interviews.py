"""Interview lifecycle: create, join, run, persist.

This is the half of Milestone 3 that was missing. Until now a bot could only be
started by a developer running a script against a public Daily room whose URL
was printed to a terminal.

Two things change here, and the second is a security fix:

- **FastAPI spawns the bot**, one process per interview, when the candidate
  actually joins. Creating an interview a week early costs nothing.
- **Rooms are private.** Access is via a meeting ID and password the employer
  gives the candidate, exchanged for a short-lived Daily token minted here.
  Candidates never see a Daily URL, and a leaked room link is not enough to walk
  into someone's interview.
"""

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import require_admin
from app.capacity import AtCapacity, registry
from app.db import (
    BlueprintStatus,
    Candidate,
    FeedbackStatus,
    Interview,
    InterviewStatus,
    Job,
    get_sessionmaker,
)
from app.meeting import (
    new_meeting_id,
    new_password,
    normalise_meeting_id,
    passwords_match,
)
from bot.config import Settings
from bot.services.daily import create_meeting_token, create_room
from shared.contracts import FeedbackReport

router = APIRouter(tags=["interviews"])

#: Guards every route on this module except the candidate's own join. Creating
#: an interview mints a paid Daily room, and reading one returns the full
#: transcript — neither can be open on a public URL. The candidate's route is
#: deliberately outside this: they have no account, and their credential is the
#: meeting ID and password.
ADMIN_ONLY = [Depends(require_admin)]

BACKEND_DIR = Path(__file__).resolve().parents[1]

#: How long the room stays bookable. Generous: interviews get rescheduled, and
#: a room that expires before the candidate arrives is a wasted slot.
ROOM_TTL_HOURS = 24

#: Candidate tokens outlive the interview only enough to survive a reconnect
#: after a dropped connection.
CANDIDATE_TOKEN_MINUTES = 90
BOT_TOKEN_MINUTES = 90


class InterviewCreated(BaseModel):
    interview_id: str
    candidate_id: str
    meeting_id: str
    password: str
    status: str
    expires_at: datetime


class JoinRequest(BaseModel):
    meeting_id: str
    password: str
    #: Consent is a precondition, not a formality: the candidate is told the
    #: interview is conducted by an AI and recorded, and that the transcript
    #: goes to the employer. No bot is ever started without it, so there is no
    #: path where someone is recorded before agreeing.
    consent: bool = False
    #: IANA timezone from the candidate's browser, e.g. "Asia/Kolkata". Used so
    #: the opening greeting matches the clock they are looking at rather than
    #: the server's, which is UTC and belongs to nobody. Untrusted and optional:
    #: it is validated where it is used, and an interview never fails over it.
    timezone: str | None = None


class FocusAbsence(BaseModel):
    """One stretch where the interview page was not the thing in front of them.

    `kind` records which event opened the absence: "hidden" when the tab or
    window stopped being visible, "blur" when it stayed visible but something
    else took focus. Both are the same fact to a reviewer and are counted
    together; the distinction is kept because they are not the same event and
    flattening them here would lose that for good.
    """

    at_seconds: float
    seconds: float
    kind: str = "hidden"


class FocusReport(BaseModel):
    """Absences from one candidate's page, sent by that page.

    Authenticated exactly as `/interviews/join` is, with the candidate's own
    meeting credentials, because it is the same browser reporting about the same
    interview and there is no other secret it holds.
    """

    meeting_id: str
    password: str
    absences: list[FocusAbsence] = Field(default_factory=list)


class JoinResponse(BaseModel):
    """What the candidate's browser needs to enter the call.

    Deliberately does not include anything about the employer, the blueprint or
    the evaluation — the candidate's page has no business knowing what it is
    being scored on.
    """

    interview_id: str
    room_url: str
    token: str
    candidate_name: str | None
    role_title: str
    #: `technical` (MITRA interviews) or `hr` (a person does, and no bot joins).
    #: Track I's candidate app reads this to pick its mode (docs/DEV_PLAN.md
    #: section 5).
    round: str = "technical"


class InterviewView(BaseModel):
    interview_id: str
    candidate_id: str
    meeting_id: str
    password: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    failure_reason: str | None = None
    transcript: dict | None = None
    section_outcomes: list[dict] | None = None
    session_metrics: dict | None = None
    #: Context for a reviewer, never an input to the report. See `db.Interview`.
    focus_events: list[dict] | None = None
    feedback_report: dict | None = None
    feedback_status: str = "pending"
    feedback_error: str | None = None
    feedback_generated_at: datetime | None = None

    #: The recording, for a person to watch back. The status is what the panel
    #: renders from: there is a state for "never recorded", one for "still being
    #: fetched from Daily" and one for "we could not get it", and each of those
    #: is a different sentence to show rather than a player that will not play.
    recording_status: str = "not_recorded"
    recording_error: str | None = None
    recording_expires_at: datetime | None = None
    recording_deleted_at: datetime | None = None
    recording_bytes: int | None = None
    #: Seconds to subtract from a transcript timestamp to land in the right
    #: place in the video. Without it, clicking a line seeks to roughly the
    #: right moment and quietly misses by however long the bot took to join.
    recording_offset_seconds: float | None = None


def _settings() -> Settings:
    return Settings.load()


def _with_latency_modes(session_metrics: dict | None) -> dict | None:
    """Fill in the two latency modes for an interview recorded before they existed.

    Time to first audio is bimodal, and until now only the median was stored. A
    median cannot describe two modes: when the share of turns that waited out
    the endpointing fallback crossed one half, it stepped from 2.04s to 5.27s
    while both modes stayed exactly where they were.

    The per-turn samples were always stored, so this is derivable rather than
    lost. Computed on read by the same function that computes it on write, so
    the two cannot drift, and nothing is written back.
    """
    if not session_metrics:
        return session_metrics
    summary = session_metrics.get("latency_summary")
    if not isinstance(summary, dict) or "fallback_share" in summary:
        return session_metrics

    from bot.observers import bimodal_latency

    turns = session_metrics.get("turns")
    if not isinstance(turns, list):
        return session_metrics
    enriched = dict(session_metrics)
    enriched["latency_summary"] = {**summary, **bimodal_latency(turns)}
    return enriched


def _validated_report(interview_id: str, stored: dict | None) -> dict | None:
    """Re-validate a stored report on the way out, so computed fields exist.

    A report is written once as JSON and never rewritten. Computed fields are
    serialised at the moment of that write, so a field added to the contract
    afterwards is missing from every report already in the database, forever:
    the counts it derives from are stored, but nothing derives it. Validating
    here recomputes them from those counts. Nothing is written back.

    That matters concretely. `ConversationHealth.degraded` is stored and its
    sentence was not, so a panel that trusted the report for both would announce
    a problem and then have nothing to say about it.

    **A report that no longer matches the contract must not take the interview
    down with it.** The deployed database may hold reports written under an
    older shape, and the transcript, the metrics and the status are exactly what
    somebody is looking at when they open an interview that went wrong. Those
    stay readable; the report is left out and the reason is logged.
    """
    if not stored:
        return None
    try:
        return FeedbackReport.model_validate(stored).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[{interview_id}] stored feedback report does not match the current "
            f"contract, so it was left out of the response: {exc}"
        )
        return None


@router.post("/candidates/{candidate_id}/interviews", response_model=InterviewCreated, dependencies=ADMIN_ONLY)
async def create_interview(candidate_id: str) -> InterviewCreated:
    """Book an interview for a candidate whose blueprint is ready."""
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"No candidate '{candidate_id}'.")
        if candidate.blueprint_status != BlueprintStatus.READY:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Blueprint is '{candidate.blueprint_status}', not ready. "
                    "An interview cannot start without a plan to run."
                ),
            )

        # One live session per candidate. Without this, clicking Start twice
        # created two interviews with two Daily rooms and two credential pairs
        # — and whichever set the candidate was sent, the other room sat paid
        # for and empty. A candidate can only be in one interview anyway.
        existing = await session.scalar(
            select(Interview).where(
                Interview.candidate_id == candidate_id,
                Interview.status.in_(
                    [InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS]
                ),
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This candidate already has a session ({existing.meeting_id}) "
                    f"that is {existing.status}. Cancel it or wait for it to "
                    "finish before starting another."
                ),
            )

    settings = _settings()
    room = await create_room(
        api_key=settings.daily_api_key,
        expiry_seconds=ROOM_TTL_HOURS * 3600,
        privacy="private",
    )

    interview_id = f"int_{uuid.uuid4().hex[:12]}"
    expires_at = datetime.now(UTC) + timedelta(hours=ROOM_TTL_HOURS)

    async with get_sessionmaker()() as session:
        interview = Interview(
            id=interview_id,
            candidate_id=candidate_id,
            meeting_id=new_meeting_id(),
            password=new_password(),
            daily_room_name=room.name,
            daily_room_url=room.url,
            room_expires_at=expires_at,
            status=InterviewStatus.SCHEDULED,
        )
        session.add(interview)
        await session.commit()
        credentials = (interview.meeting_id, interview.password)

    # The password is never logged, here or anywhere else.
    logger.info(f"[{interview_id}] scheduled for candidate {candidate_id}")
    return InterviewCreated(
        interview_id=interview_id,
        candidate_id=candidate_id,
        meeting_id=credentials[0],
        password=credentials[1],
        status=InterviewStatus.SCHEDULED,
        expires_at=expires_at,
    )


@router.post("/interviews/join", response_model=JoinResponse)
async def join_interview(request: JoinRequest) -> JoinResponse:
    """Exchange meeting credentials for a short-lived Daily token.

    The bot is spawned here rather than at scheduling time, so it joins seconds
    after the candidate does instead of idling in an empty room.
    """
    meeting_id = normalise_meeting_id(request.meeting_id)

    # One message for every failure mode below. Distinguishing "no such meeting"
    # from "wrong password" would let someone probe for valid meeting ids.
    invalid = HTTPException(
        status_code=401, detail="That meeting ID and password do not match an interview."
    )
    if not meeting_id:
        raise invalid

    if not request.consent:
        raise HTTPException(
            status_code=400,
            detail="The interview cannot start until you accept the recording notice.",
        )

    async with get_sessionmaker()() as session:
        interview = await session.scalar(
            select(Interview).where(Interview.meeting_id == meeting_id)
        )
        if interview is None or not passwords_match(request.password, interview.password):
            raise invalid

        if interview.status in (InterviewStatus.COMPLETED, InterviewStatus.EXPIRED):
            raise HTTPException(
                status_code=410, detail="This interview has already taken place."
            )

        room_expiry = interview.room_expires_at
        if room_expiry.tzinfo is None:
            room_expiry = room_expiry.replace(tzinfo=UTC)
        if room_expiry < datetime.now(UTC):
            interview.status = InterviewStatus.EXPIRED
            await session.commit()
            raise HTTPException(
                status_code=410, detail="This interview link has expired."
            )

        candidate = await session.get(Candidate, interview.candidate_id)
        already_running = interview.status == InterviewStatus.IN_PROGRESS
        # An HR round is two people in a room: no bot, so no bot slot to claim
        # and nothing to spawn. The interview is simply under way once someone
        # is in it.
        is_hr_round = interview.round == "hr"
        if is_hr_round and interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)

        # Checked before minting tokens or recording consent. Turning a
        # candidate away is bad; taking their consent and their Daily token and
        # then failing to seat them is worse.
        if not already_running and not is_hr_round:
            try:
                registry.claim()
            except AtCapacity as exc:
                logger.warning(f"[{interview.id}] join refused — {exc}")
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "All interview slots are busy right now. Your link is "
                        "still valid. Please try again in a few minutes."
                    ),
                ) from exc

        # Recorded against the interview it applies to, at the moment it was
        # given. First acceptance wins — a reconnect is not a fresh consent.
        if interview.consent_accepted_at is None:
            interview.consent_accepted_at = datetime.now(UTC)
        if request.timezone and not interview.candidate_timezone:
            interview.candidate_timezone = request.timezone[:64]
        await session.commit()
        interview_id = interview.id
        room_url = interview.daily_room_url
        room_name = interview.daily_room_name

    blueprint = (candidate.blueprint or {}) if candidate else {}
    role_title = (blueprint.get("evaluation_spec") or {}).get("role_title")
    if not role_title and candidate is not None:
        # Candidates added through the console have no blueprint until their
        # technical round is booked; the job's own title is the right label.
        async with get_sessionmaker()() as session:
            job = await session.get(Job, candidate.job_id)
            role_title = job.title if job else None
    role_title = role_title or "this role"

    settings = _settings()
    candidate_token = await create_meeting_token(
        api_key=settings.daily_api_key,
        room_name=room_name,
        expiry_seconds=CANDIDATE_TOKEN_MINUTES * 60,
        is_owner=False,
        user_name=candidate.name if candidate else None,
    )

    # A candidate refreshing the page must not start a second bot in the room,
    # and an HR round never gets one.
    if not already_running and not is_hr_round:
        await _start_bot(interview_id=interview_id, candidate_id=interview.candidate_id,
                        room_url=room_url, room_name=room_name, settings=settings,
                        timezone=request.timezone)

    return JoinResponse(
        interview_id=interview_id,
        room_url=room_url,
        token=candidate_token,
        candidate_name=candidate.name if candidate else None,
        role_title=role_title,
        round=interview.round,
    )


async def _start_bot(
    *, interview_id: str, candidate_id: str, room_url: str, room_name: str,
    settings: Settings, timezone: str | None = None,
) -> None:
    """Spawn one bot process for one interview.

    The process outlives this request and writes its own transcript to the
    database when it ends. Nothing here waits for it.
    """
    bot_token = await create_meeting_token(
        api_key=settings.daily_api_key,
        room_name=room_name,
        expiry_seconds=BOT_TOKEN_MINUTES * 60,
        is_owner=True,
        user_name="Interviewer",
    )

    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is not None:
            interview.status = InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)
            await session.commit()

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bot.run_bot",
            "--room-url", room_url,
            "--token", bot_token,
            "--session-id", interview_id,
            "--interview-id", interview_id,
            "--blueprint-id", candidate_id,
            *(["--timezone", timezone] if timezone else []),
            cwd=str(BACKEND_DIR),
            env={**os.environ},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] could not spawn bot: {exc}")
        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is not None:
                interview.status = InterviewStatus.FAILED
                interview.failure_reason = f"Bot failed to start: {exc}"
                await session.commit()
        raise HTTPException(
            status_code=500, detail="The interviewer could not be started."
        ) from exc

    registry.register(interview_id, process)
    logger.info(f"[{interview_id}] bot spawned")


#: A defensive ceiling on what one interview may store, not a judgement about
#: what is normal. A page in a redraw loop could otherwise append for forty
#: minutes; a real interview produces a handful. Nothing is inferred from
#: hitting it, and the reviewer is told the list was truncated rather than shown
#: a total that quietly stopped counting.
MAX_FOCUS_ABSENCES = 200

#: Absences longer than the longest interview we would ever run are a broken
#: clock, not a candidate who left for two hours.
MAX_ABSENCE_SECONDS = 3 * 60 * 60


@router.post("/interviews/focus")
async def record_focus(report: FocusReport) -> dict:
    """Record that the interview page stopped being in front of the candidate.

    **This is a fact the browser reported and nothing more.** It carries no
    score, no threshold and no percentage, and it reaches nothing that decides
    anything: not the brain, not the prompt, not the judge, not the scorer. It
    is stored beside the interview and shown to a reviewer as a count and a
    duration, and what it means is theirs to judge.

    It also catches only one of several ways a candidate could read an answer,
    the same-device one, and sees nothing of a phone, a second monitor with the
    browser still focused, or a person in the room. Anyone reading this data
    should know that before drawing anything from it.

    Public, and authenticated the way `/interviews/join` is: the candidate's own
    meeting credentials. Appended rather than replaced, because the page sends
    each absence as it ends and a lost connection must not take the earlier ones
    with it.
    """
    meeting_id = normalise_meeting_id(report.meeting_id)
    # One message for every failure, exactly as the join endpoint does it, so
    # this cannot become a way to probe for valid meeting ids.
    invalid = HTTPException(
        status_code=401, detail="That meeting ID and password do not match an interview."
    )
    if not meeting_id:
        raise invalid

    if not report.absences:
        return {"recorded": 0}

    async with get_sessionmaker()() as session:
        interview = await session.scalar(
            select(Interview).where(Interview.meeting_id == meeting_id)
        )
        if interview is None or not passwords_match(report.password, interview.password):
            raise invalid

        stored = list(interview.focus_events or [])
        room = MAX_FOCUS_ABSENCES - len(stored)
        if room <= 0:
            return {"recorded": 0, "truncated": True}

        accepted = [
            {
                "at_seconds": round(max(0.0, a.at_seconds), 1),
                "seconds": round(a.seconds, 1),
                "kind": a.kind if a.kind in ("hidden", "blur") else "hidden",
            }
            for a in report.absences
            if 0 < a.seconds <= MAX_ABSENCE_SECONDS
        ][:room]

        if not accepted:
            return {"recorded": 0}

        # Reassigned rather than mutated in place: SQLAlchemy does not track
        # mutation inside a JSON column, so appending to the existing list would
        # leave the change unwritten.
        interview.focus_events = stored + accepted
        await session.commit()
        logger.info(
            f"[{interview.id}] recorded {len(accepted)} focus absence(s); "
            f"{len(interview.focus_events)} in total"
        )
        return {"recorded": len(accepted)}


@router.get("/interviews/{interview_id}", response_model=InterviewView, dependencies=ADMIN_ONLY)
async def get_interview(interview_id: str) -> InterviewView:
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")
        return InterviewView(
            interview_id=interview.id,
            candidate_id=interview.candidate_id,
            meeting_id=interview.meeting_id,
            password=interview.password,
            status=interview.status,
            created_at=interview.created_at,
            started_at=interview.started_at,
            ended_at=interview.ended_at,
            failure_reason=interview.failure_reason,
            transcript=interview.transcript,
            section_outcomes=interview.section_outcomes,
            session_metrics=_with_latency_modes(interview.session_metrics),
            focus_events=interview.focus_events,
            feedback_report=_validated_report(interview.id, interview.feedback_report),
            feedback_status=interview.feedback_status,
            feedback_error=interview.feedback_error,
            feedback_generated_at=interview.feedback_generated_at,
            recording_status=interview.recording_status,
            recording_error=interview.recording_error,
            recording_expires_at=interview.recording_expires_at,
            recording_deleted_at=interview.recording_deleted_at,
            recording_bytes=interview.recording_bytes,
            recording_offset_seconds=interview.recording_offset_seconds,
        )


@router.post("/interviews/{interview_id}/feedback", dependencies=ADMIN_ONLY)
async def regenerate_feedback(interview_id: str, background: BackgroundTasks) -> dict:
    """Recompute the report from the stored transcript.

    Serves two cases: retrying one that failed, and rebuilding one that
    succeeded — after the prompt improves, or when the employer simply wants
    another read of the same conversation.

    It is never the trigger. Scoring happens by itself the moment the transcript
    is saved, so an interview nobody opens still gets a report.

    Always scores the transcript that is already stored. This cannot reach a
    live interview or a partial conversation: the guard below only lets a
    completed one through, and the transcript is written once and not amended.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")
        if interview.status != InterviewStatus.COMPLETED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This interview is {interview.status}. Only a completed "
                    "interview has a full transcript to score."
                ),
            )
        if interview.feedback_status == FeedbackStatus.GENERATING:
            raise HTTPException(
                status_code=409, detail="Scoring is already running for this interview."
            )
        interview.feedback_status = FeedbackStatus.PENDING
        interview.feedback_error = None
        await session.commit()

    from feedback.run import generate_feedback

    background.add_task(generate_feedback, interview_id)
    return {"interview_id": interview_id, "feedback_status": FeedbackStatus.PENDING}


@router.delete("/interviews/{interview_id}", dependencies=ADMIN_ONLY)
async def cancel_interview(interview_id: str) -> dict:
    """Cancel a session that has not started, freeing the candidate for a new one.

    Only a scheduled session can be cancelled. A live interview is someone
    mid-sentence in a room, and a stray click here would cut them off; ending
    one of those is the bot's job when the conversation closes. A completed
    interview is a record, and records are not cancelled.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")

        if interview.status != InterviewStatus.SCHEDULED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This session is {interview.status} and cannot be cancelled. "
                    "Only a session that has not started yet can be."
                ),
            )

        candidate_id = interview.candidate_id
        await session.delete(interview)
        await session.commit()

    logger.info(f"[{interview_id}] cancelled before it started")
    return {"cancelled": interview_id, "candidate_id": candidate_id}


@router.get("/candidates/{candidate_id}/interviews", dependencies=ADMIN_ONLY)
async def list_interviews(candidate_id: str) -> list[dict]:
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(Interview)
            .where(Interview.candidate_id == candidate_id)
            .order_by(Interview.created_at.desc())
        )
        return [
            {
                "interview_id": row.id,
                "meeting_id": row.meeting_id,
                # Both halves of the credential, or the candidate page can show
                # a meeting ID that opens nothing. This is an admin-only route,
                # and the password is the thing the employer has to pass on.
                "password": row.password,
                "status": row.status,
                "created_at": row.created_at,
                "ended_at": row.ended_at,
            }
            for row in rows
        ]
