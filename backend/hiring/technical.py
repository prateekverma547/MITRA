"""Booking the technical round, and sending its result to the BU head. Track H.

The technical round is MITRA's interview, and Track I owns it: the room, the
two links, the live view and the report. The seam between the tracks is
`book_technical(candidate, duration_minutes) -> TwoLinks` (docs/DEV_PLAN.md
section 5). Until Track I's lands, `book_technical` below is a stub that
returns placeholder links and says so (`stub=True`), so HR's screen can be
built and tested end to end without pretending an interview was arranged.

What this module owns is the hiring side of it:
- HR books (`ready_for_technical` -> `technical_booked`) or cancels the booking
  (back to `ready_for_technical`, with a reason);
- HR sends the finished result to the BU head (`technical_done` ->
  `with_bu_head`), with an optional note that is kept as the move's reason.

`technical_booked` -> `technical_done` is a system move made by Track I when
the report is ready. Nothing here makes it, and nothing here fakes it.
"""

from dataclasses import asdict, dataclass

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import Candidate, Interview, Job, get_sessionmaker
from hiring.api import router
from hiring.models import AuditEvent, DecisionRecord
from hiring.stages import StageError, audit, move
from org.auth import current_user, job_scope, require_role
from org.models import User
from shared.stages import CandidateStage as S
from shared.stages import Role

_hr = require_role(Role.HR)

MIN_DURATION = 20
MAX_DURATION = 90
DEFAULT_DURATION = 40

#: The audit action that records a booking and its links, so the screen can
#: show them again after a reload. Track I's real booking will own a proper
#: record (an interview row with `round="technical"`); until then the audit
#: log is the honest place for a stub's output.
BOOKED_ACTION = "technical_round_booked"


# ---- the seam (Track I provides the real one) ------------------------------------------------


@dataclass(frozen=True)
class TwoLinks:
    """What booking a technical round hands back to HR."""

    #: The candidate's join link.
    candidate_link: str
    #: The live view for HR and the job's BU head, behind the console login.
    live_view_link: str
    #: True while these are placeholders from the stub below.
    stub: bool = False


async def book_technical(candidate: Candidate, duration_minutes: int) -> TwoLinks:
    """STUB until Track I's booking lands. Returns placeholder links.

    Track I replaces this with the real booking: a room, a candidate join link
    and a live view link, with MITRA planned from the JD, the spec, the CV and
    the HR round's findings. Keep the signature.
    """
    return TwoLinks(
        candidate_link=f"https://example.invalid/join/{candidate.id}?minutes={duration_minutes}",
        live_view_link=f"https://example.invalid/live/{candidate.id}",
        stub=True,
    )


# ---- helpers -----------------------------------------------------------------------------------


def default_duration(job: Job) -> int:
    """The job's configured interview length, or 40 minutes, within 20-90."""
    raw = (job.evaluation_spec or {}).get("duration_minutes")
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DURATION
    return max(MIN_DURATION, min(MAX_DURATION, minutes))


async def technical_report(session, candidate_id: str) -> tuple[Interview, dict] | None:
    """The newest finished technical interview with a report, if there is one."""
    row = (
        await session.execute(
            select(Interview)
            .where(
                Interview.candidate_id == candidate_id,
                Interview.round == "technical",
                Interview.feedback_report.isnot(None),
            )
            .order_by(Interview.created_at.desc())
        )
    ).scalars().first()
    return (row, row.feedback_report) if row is not None else None


RECOMMENDATION_WORDS = {
    "strong_evidence_for": "strong evidence for",
    "some_evidence_for": "some evidence for",
    "mixed": "mixed evidence",
    "limited_evidence": "limited evidence",
    "insufficient_signal": "not enough signal to say",
}


async def technical_recommendation(session, candidate_id: str) -> str | None:
    """MITRA's reading of the technical round, recorded beside the person's decision."""
    found = await technical_report(session, candidate_id)
    if found is None:
        return None
    signal = (found[1] or {}).get("recommendation")
    if not signal:
        return None
    return f"Technical round report: {RECOMMENDATION_WORDS.get(signal, signal)}."


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


async def _booking(session, candidate_id: str) -> dict | None:
    """The current booking's details, if the candidate is booked."""
    event = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == BOOKED_ACTION, AuditEvent.subject_id == candidate_id)
            .order_by(AuditEvent.id.desc())
        )
    ).scalars().first()
    if event is None:
        return None
    detail = dict(event.detail or {})
    by = await session.get(User, event.actor_user_id) if event.actor_user_id else None
    return detail | {
        "booked_at": event.created_at.isoformat() if event.created_at else None,
        "booked_by": by.name if by else None,
    }


async def _sent(session, candidate_id: str) -> dict | None:
    """When, by whom and with what note the result went to the BU head."""
    record = (
        await session.execute(
            select(DecisionRecord)
            .where(DecisionRecord.candidate_id == candidate_id, DecisionRecord.to_stage == S.WITH_BU_HEAD.value)
            .order_by(DecisionRecord.id.desc())
        )
    ).scalars().first()
    if record is None:
        return None
    by = await session.get(User, record.actor_user_id) if record.actor_user_id else None
    return {
        "at": record.created_at.isoformat() if record.created_at else None,
        "by": by.name if by else None,
        "note": record.reason,
    }


async def _view(session, user: User, candidate: Candidate, job: Job) -> dict:
    stage = S(candidate.stage)
    is_hr = Role(user.role) is Role.HR
    booking = await _booking(session, candidate.id) if stage is S.TECHNICAL_BOOKED else None
    report = await technical_report(session, candidate.id)
    return {
        "candidate": {"id": candidate.id, "name": candidate.name or "Unnamed candidate", "stage": candidate.stage},
        "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
        "duration": {"default": default_duration(job), "min": MIN_DURATION, "max": MAX_DURATION},
        "booking": booking,
        "has_report": report is not None,
        "sent_to_bu_head": await _sent(session, candidate.id),
        "can_book": is_hr and stage is S.READY_FOR_TECHNICAL,
        "can_cancel": is_hr and stage is S.TECHNICAL_BOOKED,
        "can_send": is_hr and stage is S.TECHNICAL_DONE,
    }


# ---- routes ------------------------------------------------------------------------------------


class BookRequest(BaseModel):
    duration_minutes: int | None = Field(default=None, ge=MIN_DURATION, le=MAX_DURATION)


class CancelRequest(BaseModel):
    reason: str


class SendRequest(BaseModel):
    note: str | None = None


@router.get("/candidates/{candidate_id}/technical")
async def get_technical(candidate_id: str, user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/technical", status_code=201)
async def book(candidate_id: str, body: BookRequest, user: User = Depends(_hr)) -> dict:
    """Book the technical round. Returns the two links (placeholders while stubbed)."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) is not S.READY_FOR_TECHNICAL:
            raise HTTPException(status_code=409, detail="Only a candidate ready for the technical round can be booked.")
        minutes = body.duration_minutes or default_duration(job)
        links = await book_technical(candidate, minutes)
        try:
            await move(session, candidate, S.TECHNICAL_BOOKED, by=user)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(
            session, BOOKED_ACTION, by=user, subject_type="candidate", subject_id=candidate.id,
            detail=asdict(links) | {"duration_minutes": minutes},
        )
        await session.commit()
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/technical/cancel")
async def cancel(candidate_id: str, body: CancelRequest, user: User = Depends(_hr)) -> dict:
    """Cancel a booking. The candidate can be booked again; the reason is kept."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Say why the technical round is cancelled.")
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) is not S.TECHNICAL_BOOKED:
            raise HTTPException(status_code=409, detail="There is no booked technical round to cancel.")
        try:
            await move(session, candidate, S.READY_FOR_TECHNICAL, by=user, reason=reason)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(session, "technical_round_cancelled", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"reason": reason})
        await session.commit()
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/send-to-bu-head")
async def send_to_bu_head(candidate_id: str, body: SendRequest, user: User = Depends(_hr)) -> dict:
    """HR hands the finished result to the BU head, who decides. HR cannot reject here."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) is not S.TECHNICAL_DONE:
            raise HTTPException(status_code=409, detail="Only a candidate whose technical round is done can be sent to the BU head.")
        try:
            await move(
                session, candidate, S.WITH_BU_HEAD, by=user, reason=(body.note or "").strip() or None,
                model_recommendation=await technical_recommendation(session, candidate.id),
            )
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await session.commit()
        return await _view(session, user, candidate, job)
