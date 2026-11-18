"""The HR round: a person interviews the candidate, inside MITRA's own room.

Phase 2 of docs/TRACK_H.md. No bot ever joins: `interviews.round == "hr"`
makes the join path skip it (app/interviews.py). The flow:

1. HR books it from a candidate at `ready_for_hr`: a private Daily room,
   candidate credentials (the same meeting ID and password the technical
   round uses), and the candidate moves to `hr_booked`.
2. HR sends the candidate the meeting ID, password and join page.
3. HR joins from the console as the room's owner, with the ten questions as a
   checklist and a notes box beside the call. Both save as HR works.
4. HR ends the round. The transcript and the evaluation that move the
   candidate to `hr_done` are the next step of phase 2.

Cancelling moves the candidate back to `ready_for_hr` with a reason, so they
can be booked again and the history says why.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.db import Candidate, Interview, InterviewStatus, Job, get_sessionmaker
from app.meeting import new_meeting_id, new_password
from bot.config import Settings
from bot.services import daily
from hiring.api import router
from hiring.models import HRRound, Screening
from hiring.stages import StageError, audit, move
from org.auth import current_user, job_scope, require_role
from org.models import User
from shared.stages import CandidateStage, Role

_hr = require_role(Role.HR)

#: How long the HR round's room and credentials stay valid. A day, the same as
#: the technical round's room.
ROOM_TTL_HOURS = 24
#: HR's own token into the room. Long enough for a round that runs over.
HR_TOKEN_MINUTES = 120

LIVE = (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS)


class Progress(BaseModel):
    asked: list[str] = []
    notes: str = ""


class CancelRequest(BaseModel):
    reason: str


def _join_url() -> str:
    return f"{(os.environ.get('APP_BASE_URL') or '').rstrip('/')}/join"


async def _candidate(session, user: User, candidate_id: str) -> tuple[Candidate, Job]:
    row = (
        await session.execute(
            select(Candidate, Job).join(Job, Candidate.job_id == Job.id)
            .where(Candidate.id == candidate_id, job_scope(user))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such candidate.")
    return row[0], row[1]


async def _latest(session, candidate_id: str) -> tuple[Interview, HRRound] | None:
    row = (
        await session.execute(
            select(Interview, HRRound)
            .join(HRRound, HRRound.interview_id == Interview.id)
            .where(Interview.candidate_id == candidate_id, Interview.round == "hr")
            .order_by(Interview.created_at.desc(), Interview.id.desc())
        )
    ).first()
    return (row[0], row[1]) if row else None


async def _view(session, user: User, candidate: Candidate, job: Job) -> dict:
    found = await _latest(session, candidate.id)
    screening = (
        await session.execute(select(Screening).where(Screening.candidate_id == candidate.id))
    ).scalar_one_or_none()
    questions = (screening.hr_questions or {}).get("questions", []) if screening else []
    is_hr = Role(user.role) is Role.HR
    base = {
        "candidate": {"id": candidate.id, "name": candidate.name, "stage": candidate.stage},
        "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
        "questions": questions,
        "can_book": is_hr and candidate.stage == CandidateStage.READY_FOR_HR,
    }
    if found is None:
        return base | {"round": None}
    interview, hr = found
    live = interview.status in LIVE
    return base | {
        "round": {
            "interview_id": interview.id,
            "status": interview.status,
            # The password is for the person who sends it. Nobody else sees it.
            "meeting_id": interview.meeting_id if is_hr else None,
            "password": interview.password if is_hr else None,
            "join_url": _join_url() if is_hr else None,
            "expires_at": interview.room_expires_at.isoformat() if interview.room_expires_at else None,
            "asked": hr.questions_asked or [],
            "notes": hr.notes or "",
            "started_at": interview.started_at.isoformat() if interview.started_at else None,
            "ended_at": (hr.ended_at or interview.ended_at).isoformat() if (hr.ended_at or interview.ended_at) else None,
            "can_join": is_hr and live,
            "can_end": is_hr and interview.status == InterviewStatus.IN_PROGRESS,
            "can_cancel": is_hr and interview.status == InterviewStatus.SCHEDULED,
            "can_edit_notes": is_hr and interview.status != InterviewStatus.EXPIRED,
        },
    }


async def _live_round(session, candidate_id: str) -> tuple[Interview, HRRound]:
    found = await _latest(session, candidate_id)
    if found is None or found[0].status not in LIVE:
        raise HTTPException(status_code=409, detail="There is no HR round booked for this candidate.")
    return found


@router.get("/candidates/{candidate_id}/hr-round")
async def get_hr_round(candidate_id: str, user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/hr-round", status_code=201)
async def book_hr_round(candidate_id: str, user: User = Depends(_hr)) -> dict:
    """Book the room and credentials, and move the candidate to `hr_booked`."""
    async with get_sessionmaker()() as session:
        candidate, _ = await _candidate(session, user, candidate_id)
        if candidate.stage != CandidateStage.READY_FOR_HR:
            raise HTTPException(status_code=409, detail="Only a candidate who is ready for the HR round can be booked.")
        found = await _latest(session, candidate.id)
        if found and found[0].status in LIVE:
            raise HTTPException(status_code=409, detail="This candidate already has an HR round booked.")

    settings = Settings.load()
    room = await daily.create_room(
        api_key=settings.daily_api_key, expiry_seconds=ROOM_TTL_HOURS * 3600, privacy="private"
    )

    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        interview = Interview(
            id=f"int_{uuid.uuid4().hex[:12]}",
            candidate_id=candidate.id,
            meeting_id=new_meeting_id(),
            password=new_password(),
            daily_room_name=room.name,
            daily_room_url=room.url,
            room_expires_at=datetime.now(UTC) + timedelta(hours=ROOM_TTL_HOURS),
            status=InterviewStatus.SCHEDULED,
            org_id=candidate.org_id,
            round="hr",
        )
        session.add(interview)
        await session.flush()
        session.add(HRRound(interview_id=interview.id, candidate_id=candidate.id, booked_by=user.id))
        try:
            await move(session, candidate, CandidateStage.HR_BOOKED, by=user)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(session, "hr_round_booked", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"interview_id": interview.id})
        await session.commit()
        logger.info(f"[{interview.id}] HR round booked for {candidate.id} by {user.id}")
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/hr-round/join")
async def join_hr_round(candidate_id: str, user: User = Depends(_hr)) -> dict:
    """HR's way into the room: an owner token, so HR runs the call."""
    async with get_sessionmaker()() as session:
        await _candidate(session, user, candidate_id)
        interview, hr = await _live_round(session, candidate_id)
        if interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)
        hr.hr_joined_at = hr.hr_joined_at or datetime.now(UTC)
        await session.commit()
        room_name, room_url = interview.daily_room_name, interview.daily_room_url

    settings = Settings.load()
    token = await daily.create_meeting_token(
        api_key=settings.daily_api_key, room_name=room_name,
        expiry_seconds=HR_TOKEN_MINUTES * 60, is_owner=True, user_name=user.name,
    )
    return {"room_url": room_url, "token": token}


@router.post("/candidates/{candidate_id}/hr-round/progress")
async def save_progress(candidate_id: str, body: Progress, user: User = Depends(_hr)) -> dict:
    """Autosave: which questions HR has asked, and their notes."""
    async with get_sessionmaker()() as session:
        await _candidate(session, user, candidate_id)
        found = await _latest(session, candidate_id)
        if found is None or found[0].status == InterviewStatus.EXPIRED:
            raise HTTPException(status_code=409, detail="There is no HR round to take notes on.")
        _, hr = found
        hr.questions_asked = list(dict.fromkeys(q for q in body.asked if isinstance(q, str)))[:50]
        hr.notes = body.notes[:20000]
        await session.commit()
        return {"saved_at": datetime.now(UTC).isoformat()}


@router.post("/candidates/{candidate_id}/hr-round/end")
async def end_hr_round(candidate_id: str, user: User = Depends(_hr)) -> dict:
    """HR has finished talking. The evaluation step picks it up from here."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        interview, hr = await _live_round(session, candidate_id)
        if interview.status != InterviewStatus.IN_PROGRESS:
            raise HTTPException(status_code=409, detail="The HR round has not started yet.")
        now = datetime.now(UTC)
        interview.status = InterviewStatus.COMPLETED
        interview.ended_at = now
        hr.ended_at = now
        await audit(session, "hr_round_ended", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"interview_id": interview.id, "asked": len(hr.questions_asked or [])})
        await session.commit()
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/hr-round/cancel")
async def cancel_hr_round(candidate_id: str, body: CancelRequest, user: User = Depends(_hr)) -> dict:
    """Cancel a round nobody has joined yet. The candidate can be booked again."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Say why the round is cancelled.")
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        interview, _ = await _live_round(session, candidate_id)
        if interview.status != InterviewStatus.SCHEDULED:
            raise HTTPException(status_code=409, detail="This round has started, so it can no longer be cancelled.")
        interview.status = InterviewStatus.EXPIRED
        try:
            await move(session, candidate, CandidateStage.READY_FOR_HR, by=user, reason=reason)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await session.commit()
        return await _view(session, user, candidate, job)
