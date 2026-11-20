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
from typing import Literal

from fastapi import BackgroundTasks, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import (
    Candidate,
    Interview,
    InterviewStatus,
    Job,
    RecordingStatus,
    get_sessionmaker,
)
from app.meeting import new_meeting_id, new_password
from bot.config import Settings
from bot.services import daily
from hiring import hr_evaluation as pipeline
from hiring.api import router
from hiring.models import HRRound, Screening
from hiring.presence import in_room
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

LIVE = (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS, InterviewStatus.NO_SHOW)
#: Track I's recording sweep marks any room nobody joined before it expired as
#: `no_show` (live/phase4.py), HR rounds included. For HR that is still a
#: booked round nobody came to: HR can cancel it and invite again, exactly as
#: before the status existed. So it reads as `scheduled` everywhere here.
UNJOINED = (InterviewStatus.SCHEDULED, InterviewStatus.NO_SHOW)


def _shown_status(interview: Interview) -> str:
    return InterviewStatus.SCHEDULED if interview.status == InterviewStatus.NO_SHOW else interview.status


class Progress(BaseModel):
    asked: list[str] = []
    notes: str = ""


class LiveLine(BaseModel):
    """One finished line of the live transcript, as HR's room heard it from Daily."""

    #: The room's own id for the line, so a resend is not stored twice.
    id: str = Field(max_length=80)
    speaker: Literal["hr", "candidate", "other"]
    text: str = Field(max_length=4000)
    #: Seconds since HR joined the room.
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class LiveTranscript(BaseModel):
    lines: list[LiveLine] = Field(default_factory=list, max_length=200)


#: A defensive ceiling: a 40-minute conversation is a few hundred lines.
MAX_LIVE_LINES = 4000

#: How a live transcript is attributed: Daily tags every line with the
#: participant who spoke it, so who said what is known, not inferred.
LIVE_ATTRIBUTION = {"method": "live", "confidence": "clear", "voices": 2, "other_voices": 0}
#: A phone call MITRA listens to (mode "listen"): both voices reach one microphone,
#: so the live lines tell them apart by voice (Deepgram), not by who is speaking into
#: which device. Likely, not certain; after the call the recording is checked too.
LISTEN_ATTRIBUTION = {"method": "live_one_mic", "confidence": "likely", "voices": 2, "other_voices": 0}
MODES = ("room", "listen")


class BookRequest(BaseModel):
    #: "room" (the candidate joins MITRA's voice room) or "listen" (a phone call MITRA listens to).
    mode: Literal["room", "listen"] = "room"


class JoinRequest(BaseModel):
    #: Listen mode only: HR confirms the candidate was told the call is recorded and
    #: transcribed by MITRA, and agreed. Required before MITRA listens.
    phone_consent: bool = False


class CancelRequest(BaseModel):
    #: Optional: a cancelled invitation needs no explanation to be undone.
    reason: str = ""


class NotesCompletion(BaseModel):
    reason: str


def _join_url(meeting_id: str) -> str:
    """The candidate's room (`/meet`, the console's `#/meet`). The meeting ID
    rides along so the page fills it in; the password never goes in a link."""
    return f"{(os.environ.get('APP_BASE_URL') or '').rstrip('/')}/meet?id={meeting_id}"


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
    """The live round if there is one, else the newest. Two rounds booked in the
    same second would otherwise sort by their random ids."""
    rows = (
        await session.execute(
            select(Interview, HRRound)
            .join(HRRound, HRRound.interview_id == Interview.id)
            .where(Interview.candidate_id == candidate_id, Interview.round == "hr")
            .order_by(Interview.created_at.desc(), Interview.id.desc())
        )
    ).all()
    if not rows:
        return None
    live = next((r for r in rows if r[0].status in LIVE), None)
    row = live or rows[0]
    return (row[0], row[1])


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
            "status": _shown_status(interview),
            # "room" or "listen" (a phone call MITRA listens to; the candidate never joins).
            "mode": hr.mode,
            "phone_consent_at": hr.phone_consent_at.isoformat() if hr.phone_consent_at else None,
            # The password is for the person who sends it. Nobody else sees it, and
            # nobody is sent it for a phone call.
            "meeting_id": interview.meeting_id if is_hr and hr.mode == "room" else None,
            "password": interview.password if is_hr and hr.mode == "room" else None,
            "join_url": _join_url(interview.meeting_id) if is_hr and hr.mode == "room" else None,
            "expires_at": interview.room_expires_at.isoformat() if interview.room_expires_at else None,
            "asked": hr.questions_asked or [],
            "notes": hr.notes or "",
            "started_at": interview.started_at.isoformat() if interview.started_at else None,
            "ended_at": (hr.ended_at or interview.ended_at).isoformat() if (hr.ended_at or interview.ended_at) else None,
            "can_join": is_hr and live,
            "can_end": is_hr and interview.status == InterviewStatus.IN_PROGRESS,
            "can_cancel": is_hr and interview.status in UNJOINED,
            "can_edit_notes": is_hr and interview.status != InterviewStatus.EXPIRED,
            "recording_status": interview.recording_status,
            # Who is in the room now (Daily presence): HR counts among `others`.
            "presence": (
                (await in_room(interview.daily_room_name, candidate.name)).as_dict()
                if interview.status == InterviewStatus.IN_PROGRESS else None
            ),
            "eval_status": hr.eval_status,
            "eval_error": hr.eval_error,
            "attribution": hr.attribution,
            "transcript": hr.transcript,
            "evaluation": hr.evaluation,
            "can_retry": is_hr and hr.eval_status == pipeline.FAILED,
            "can_complete_on_notes": is_hr and hr.eval_status == pipeline.FAILED
            and candidate.stage == CandidateStage.HR_BOOKED,
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
async def book_hr_round(candidate_id: str, body: BookRequest | None = None, user: User = Depends(_hr)) -> dict:
    """Book the room and credentials, and move the candidate to `hr_booked`.

    `mode: "listen"`: HR will call the candidate on their own phone and MITRA listens
    through HR's microphone. The room is still made (it records and transcribes), but
    the candidate is never sent its meeting ID and password."""
    mode = (body or BookRequest()).mode
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
        session.add(HRRound(interview_id=interview.id, candidate_id=candidate.id, booked_by=user.id, mode=mode))
        try:
            await move(session, candidate, CandidateStage.HR_BOOKED, by=user)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(session, "hr_round_booked", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"interview_id": interview.id, "mode": mode})
        await session.commit()
        logger.info(f"[{interview.id}] HR round booked for {candidate.id} by {user.id} ({mode})")
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/hr-round/join")
async def join_hr_round(candidate_id: str, body: JoinRequest | None = None, user: User = Depends(_hr)) -> dict:
    """HR's way into the room: an owner token, so HR runs the call.

    In listen mode HR must first confirm the candidate agreed to the call being
    recorded and transcribed: they never see MITRA's consent screen."""
    body = body or JoinRequest()
    async with get_sessionmaker()() as session:
        await _candidate(session, user, candidate_id)
        interview, hr = await _live_round(session, candidate_id)
        if hr.mode == "listen":
            if not body.phone_consent and hr.phone_consent_at is None:
                raise HTTPException(status_code=422, detail="Confirm that the candidate agreed to the call being "
                                                            "recorded and transcribed before MITRA listens.")
            hr.phone_consent_at = hr.phone_consent_at or datetime.now(UTC)
        if interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)
        hr.hr_joined_at = hr.hr_joined_at or datetime.now(UTC)
        # HR's token starts the recording (decision 27); from here the sweep
        # collects it once Daily has finished it.
        if interview.recording_status == RecordingStatus.NOT_RECORDED:
            interview.recording_status = RecordingStatus.RECORDING
            interview.recording_started_at = datetime.now(UTC)
        await session.commit()
        room_name, room_url, mode = interview.daily_room_name, interview.daily_room_url, hr.mode

    settings = Settings.load()
    token = await daily.create_meeting_token(
        api_key=settings.daily_api_key, room_name=room_name,
        expiry_seconds=HR_TOKEN_MINUTES * 60, is_owner=True, user_name=user.name,
        start_cloud_recording=True,
    )
    # HR's room starts Daily's live transcription with this owner token. In listen
    # mode both voices reach HR's one microphone: the room asks Deepgram to tell
    # the voices apart (diarize).
    return {"room_url": room_url, "token": token, "transcribe": True, "mode": mode}


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


@router.post("/candidates/{candidate_id}/hr-round/transcript")
async def save_live_transcript(candidate_id: str, body: LiveTranscript, user: User = Depends(_hr)) -> dict:
    """Append finished lines of the live transcript, sent by HR's room as they are spoken.

    Daily transcribes the call and tags each line with the participant who
    said it, so HR and the candidate are told apart for certain. When the
    round ends, the evaluation reads this transcript directly instead of
    waiting for the recording and transcribing it again.
    """
    async with get_sessionmaker()() as session:
        await _candidate(session, user, candidate_id)
        interview, hr = await _live_round(session, candidate_id)
        stored = list(hr.transcript or [])
        seen = {t.get("id") for t in stored if t.get("id")}
        fresh = [line for line in body.lines if line.id not in seen and line.text.strip()]
        for line in fresh[: max(0, MAX_LIVE_LINES - len(stored))]:
            stored.append({
                "index": len(stored), "id": line.id, "speaker": line.speaker, "text": line.text.strip(),
                "start": round(line.start, 2), "end": round(max(line.end, line.start), 2),
            })
        # Reassigned, not mutated: SQLAlchemy does not see changes inside a JSON value.
        hr.transcript = stored
        others = {line.speaker for line in body.lines if line.speaker == "other"}
        base = LISTEN_ATTRIBUTION if hr.mode == "listen" else LIVE_ATTRIBUTION
        hr.attribution = {**base, "other_voices": max((hr.attribution or {}).get("other_voices", 0), len(others))}
        await session.commit()
        return {"stored": len(stored)}


@router.post("/candidates/{candidate_id}/hr-round/transcript/swap")
async def swap_live_speakers(candidate_id: str, user: User = Depends(_hr)) -> dict:
    """Listen mode: HR says the two voices are labelled the wrong way round. Both
    voices reach one microphone, so who is who is a guess the room makes; HR fixes it
    for the lines saved so far (the room fixes the ones still to come)."""
    async with get_sessionmaker()() as session:
        await _candidate(session, user, candidate_id)
        interview, hr = await _live_round(session, candidate_id)
        if hr.mode != "listen":
            raise HTTPException(status_code=409, detail="Only a phone call's speakers can be swapped.")
        flip = {"hr": "candidate", "candidate": "hr"}
        lines = [{**t, "speaker": flip.get(t.get("speaker"), t.get("speaker"))} for t in (hr.transcript or [])]
        hr.transcript = lines
        await session.commit()
        return {"swapped": len(lines)}


def has_live_transcript(hr: HRRound) -> bool:
    return pipeline.has_live_lines(hr)


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
        if has_live_transcript(hr) and not (
            pipeline.prefers_recording() and interview.recording_status == RecordingStatus.RECORDING
        ):
            # Transcribed live, and no better transcriber for the recording:
            # evaluate the live lines now.
            hr.eval_status = pipeline.EVALUATING
        elif interview.recording_status == RecordingStatus.RECORDING:
            # The recording is transcribed after the call (Deepgram nova-3,
            # measured far more accurate than the live feed) and evaluated from
            # that. The live lines stay as the fallback (hr_evaluation.advance).
            hr.eval_status = pipeline.WAITING
        else:
            hr.eval_status = pipeline.FAILED
            hr.eval_error = "This round was not recorded: HR did not join it from the console."
        await audit(session, "hr_round_ended", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"interview_id": interview.id, "asked": len(hr.questions_asked or [])})
        await session.commit()
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/hr-round/cancel")
async def cancel_hr_round(candidate_id: str, body: CancelRequest, user: User = Depends(_hr)) -> dict:
    """Cancel a round nobody has joined yet. The candidate can be invited again."""
    reason = body.reason.strip() or "Cancelled by HR"
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        interview, _ = await _live_round(session, candidate_id)
        if interview.status not in UNJOINED:
            raise HTTPException(status_code=409, detail="This round has started, so it can no longer be cancelled.")
        interview.status = InterviewStatus.EXPIRED
        try:
            await move(session, candidate, CandidateStage.READY_FOR_HR, by=user, reason=reason)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await session.commit()
        return await _view(session, user, candidate, job)


#: The decision's reason when HR skips the round; results find a skip by it and by the move.
SKIPPED = "HR round skipped"


class SkipRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


def skip_decision(decisions) -> object | None:
    """The decision that skipped the HR round, if it was skipped (newest first)."""
    for d in reversed(list(decisions)):
        if d.to_stage == CandidateStage.READY_FOR_TECHNICAL.value and d.from_stage in (
            CandidateStage.READY_FOR_HR.value, CandidateStage.HR_BOOKED.value
        ):
            return d
    return None


@router.post("/candidates/{candidate_id}/hr-round/skip")
async def skip_hr_round(candidate_id: str, body: SkipRequest, background: BackgroundTasks,
                        user: User = Depends(_hr)) -> dict:
    """Skip the HR round: the candidate goes straight to the MITRA interview round.

    Allowed before the round is booked, or while its room is still unjoined (the room
    is cancelled). Recorded as a decision by HR, with the optional reason."""
    note = body.reason.strip()
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        stage = CandidateStage(candidate.stage)
        if stage not in (CandidateStage.READY_FOR_HR, CandidateStage.HR_BOOKED):
            raise HTTPException(status_code=409, detail="The HR round can only be skipped before it has taken place.")
        if stage is CandidateStage.HR_BOOKED:
            interview, _ = await _live_round(session, candidate_id)
            if interview.status not in UNJOINED:
                raise HTTPException(status_code=409, detail="This round has started, so it can no longer be skipped.")
            interview.status = InterviewStatus.EXPIRED
        try:
            await move(session, candidate, CandidateStage.READY_FOR_TECHNICAL, by=user,
                       reason=f"{SKIPPED}: {note}" if note else SKIPPED)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(session, "hr_round_skipped", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"reason": note or None, "from": stage.value})
        await session.commit()
    # MITRA's plan is built at once, as when HR proceeds after an HR round.
    from hiring.technical import prepare_plan

    background.add_task(prepare_plan, candidate_id)
    return {"candidate_id": candidate_id, "stage": CandidateStage.READY_FOR_TECHNICAL.value}


@router.post("/candidates/{candidate_id}/hr-round/retry")
async def retry_evaluation(candidate_id: str, background: BackgroundTasks, user: User = Depends(_hr)) -> dict:
    """Try the after-call work again, from wherever it stopped."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        found = await _latest(session, candidate_id)
        if found is None or found[1].eval_status != pipeline.FAILED:
            raise HTTPException(status_code=409, detail="There is nothing to retry.")
        interview, hr = found
        if hr.transcript:
            hr.eval_status = pipeline.EVALUATING
        elif interview.recording_status == RecordingStatus.STORED:
            hr.eval_status = pipeline.TRANSCRIBING
        elif interview.recording_status == RecordingStatus.RECORDING:
            hr.eval_status = pipeline.WAITING
        else:
            raise HTTPException(status_code=409, detail="There is no recording to work from. Complete the round on your notes instead.")
        hr.eval_error = None
        await session.commit()
        interview_id = interview.id
        view = await _view(session, user, candidate, job)
    background.add_task(pipeline.advance, interview_id)
    return view


@router.post("/candidates/{candidate_id}/hr-round/complete")
async def complete_on_notes(candidate_id: str, body: NotesCompletion, user: User = Depends(_hr)) -> dict:
    """When no recording could be evaluated, HR completes the round on their notes."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Say why the round is being completed without MITRA's evaluation.")
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        found = await _latest(session, candidate_id)
        if found is None or found[1].eval_status != pipeline.FAILED:
            raise HTTPException(status_code=409, detail="Only a round MITRA could not evaluate can be completed on notes.")
        try:
            await move(session, candidate, CandidateStage.HR_DONE, by=user, reason=reason,
                       model_recommendation="No evaluation: the recording could not be evaluated.")
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await session.commit()
        return await _view(session, user, candidate, job)
