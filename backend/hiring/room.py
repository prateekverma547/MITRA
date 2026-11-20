"""The candidate's interview room: what the page needs before consent.

The room itself is the console's `#/meet` screen (frontend/console, feature
`interview-room`). It enters the call through Track I's existing candidate
endpoints, `POST /interviews/join` and `POST /interviews/focus`, unchanged.
This module adds the one thing they do not answer: before the candidate agrees
to anything, which round this is, how long it runs, and whether it is recorded,
so the consent notice describes what will actually happen.

Public, and authenticated exactly as `/interviews/join` is: the candidate's own
meeting ID and password, with one error message for every failure so it cannot
be used to probe for valid meeting IDs. It answers nothing about how the
candidate is evaluated (no plan, no competencies, no screening), the same rule
`JoinResponse` follows.
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db import Candidate, Interview, InterviewStatus, Job, get_sessionmaker
from app.meeting import normalise_meeting_id, passwords_match
from bot.config import recording_enabled
from hiring.api import router
from hiring.technical import plan_minutes
from org.models import Organisation
from shared.branding import BOT_NAME
from shared.contracts import RECORDING_RETENTION_DAYS

#: An HR conversation has no plan to take a length from.
HR_ROUND_MINUTES = 30


class RoomLookup(BaseModel):
    meeting_id: str
    password: str


class RoomFacts(BaseModel):
    #: `technical`: Mitra interviews and the session is proctored. `hr`: a
    #: person from HR, no AI in the call.
    round: str
    #: `open` (join now), `finished` (already took place), `closed` (expired,
    #: cancelled or could not run: ask HR for a new invitation).
    status: str
    role_title: str
    organisation: str | None
    #: First name only, for the greeting on the page.
    first_name: str | None
    duration_minutes: int
    #: Whether this interview is recorded, read from the same switch the
    #: recorder reads. An HR round is always recorded: it is evaluated from it.
    recording: bool
    retention_days: int
    bot_name: str


def _status(interview: Interview) -> str:
    if interview.status == InterviewStatus.COMPLETED:
        return "finished"
    if interview.status in (InterviewStatus.EXPIRED, InterviewStatus.FAILED, InterviewStatus.NO_SHOW):
        return "closed"
    expiry = interview.room_expires_at
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if expiry < datetime.now(UTC):
            return "closed"
    return "open"


@router.post("/room/lookup", response_model=RoomFacts)
async def room_lookup(body: RoomLookup) -> RoomFacts:
    """Check the candidate's credentials and say what kind of room this is.

    Changes nothing: no consent is recorded, no bot is started and the
    interview's status is left alone. Joining is `/interviews/join`.
    """
    invalid = HTTPException(status_code=401, detail="That meeting ID and password do not match an interview.")
    meeting_id = normalise_meeting_id(body.meeting_id)
    if not meeting_id:
        raise invalid
    async with get_sessionmaker()() as session:
        interview = await session.scalar(select(Interview).where(Interview.meeting_id == meeting_id))
        if interview is None or not passwords_match(body.password, interview.password):
            raise invalid
        candidate = await session.get(Candidate, interview.candidate_id)
        job = await session.get(Job, candidate.job_id) if candidate and candidate.job_id else None
        org = await session.get(Organisation, interview.org_id) if interview.org_id else None

        is_hr = interview.round == "hr"
        spec = ((candidate.blueprint or {}).get("evaluation_spec") or {}) if candidate else {}
        role_title = (job.title if job else None) or spec.get("role_title") or "this role"
        if is_hr:
            minutes = HR_ROUND_MINUTES
        elif interview.booked_duration_minutes:
            minutes = interview.booked_duration_minutes  # what HR booked (hiring/technical.py)
        elif candidate is not None and job is not None:
            minutes = plan_minutes(candidate, job)
        else:
            minutes = int(spec.get("duration_minutes") or 40)
        first = (candidate.name or "").split()[0] if candidate and candidate.name else None

        return RoomFacts(
            round="hr" if is_hr else "technical",
            status=_status(interview),
            role_title=role_title,
            organisation=org.name if org else None,
            first_name=first,
            duration_minutes=minutes,
            recording=True if is_hr else recording_enabled(),
            retention_days=RECORDING_RETENTION_DAYS,
            bot_name=BOT_NAME,
        )
