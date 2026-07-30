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
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import require_admin
from app.capacity import AtCapacity, registry
from app.db import (
    BlueprintStatus,
    Candidate,
    FeedbackStatus,
    Interview,
    InterviewStatus,
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
    feedback_report: dict | None = None
    feedback_status: str = "pending"
    feedback_error: str | None = None
    feedback_generated_at: datetime | None = None


def _settings() -> Settings:
    return Settings.load()


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

        # Checked before minting tokens or recording consent. Turning a
        # candidate away is bad; taking their consent and their Daily token and
        # then failing to seat them is worse.
        if not already_running:
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
    role_title = (blueprint.get("evaluation_spec") or {}).get("role_title", "this role")

    settings = _settings()
    candidate_token = await create_meeting_token(
        api_key=settings.daily_api_key,
        room_name=room_name,
        expiry_seconds=CANDIDATE_TOKEN_MINUTES * 60,
        is_owner=False,
        user_name=candidate.name if candidate else None,
    )

    # A candidate refreshing the page must not start a second bot in the room.
    if not already_running:
        await _start_bot(interview_id=interview_id, candidate_id=interview.candidate_id,
                        room_url=room_url, room_name=room_name, settings=settings,
                        timezone=request.timezone)

    return JoinResponse(
        interview_id=interview_id,
        room_url=room_url,
        token=candidate_token,
        candidate_name=candidate.name if candidate else None,
        role_title=role_title,
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
            session_metrics=interview.session_metrics,
            feedback_report=interview.feedback_report,
            feedback_status=interview.feedback_status,
            feedback_error=interview.feedback_error,
            feedback_generated_at=interview.feedback_generated_at,
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
