"""The technical round: MITRA's plan, the booking, and what happens after. Track H.

The interview itself is Track I's (the bot, the realtime engine, proctoring,
the report). Everything it needs already runs in this codebase, so the hiring
side drives it here rather than waiting:

1. When HR moves a candidate to `ready_for_technical`, MITRA's plan (the
   interview blueprint) is built from the job's spec, the JD and the CV, by
   the same generator the original flow uses (`app.main.generate_blueprint_task`).
   Then, still while the plan reads as being prepared, Track I's preparation
   step adds the HR round's findings as neutral probes, sizes it to the
   interview's length and writes a goal per question (`_prepare_for_interview`).
2. HR books once the plan is ready: a private room and the meeting ID and
   password to send the candidate (`Interview.round = "technical"`), returned
   in the shape of Track I's `TwoLinks` contract so their booking drops in at
   the merge. The candidate joins on the join page; the bot joins with them.
3. A background pass (`sweep_once`, run by the HR pipeline loop) records what
   happened: a finished, scored interview moves the candidate to
   `technical_done` (a system move: a fact, not a judgement); a failed or
   expired room moves them back to `ready_for_technical` with the reason.
4. HR sends the result to the BU head, who decides.

The length comes from the plan (the job's configured duration, at most 60
minutes); the booking stores what the bot runs to (`plan_timing`). HR does not
choose a length per booking.
"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.db import (
    BlueprintStatus,
    Candidate,
    FeedbackStatus,
    Interview,
    InterviewStatus,
    Job,
    get_sessionmaker,
)
from app.meeting import new_meeting_id, new_password
from bot.config import Settings
from bot.services import daily
from hiring.api import router
from hiring.models import AuditEvent, DecisionRecord
from hiring.presence import Presence, in_room
from hiring.stages import StageError, audit, move
from org.auth import current_user, job_scope, require_role
from org.models import User
from shared.stages import CandidateStage as S
from shared.stages import Role

_hr = require_role(Role.HR)

DEFAULT_DURATION = 40
ROOM_TTL_HOURS = 24
#: A finished interview whose report never arrives (the scorer was killed)
#: still moves on after this long, so nobody waits forever on a dead job.
REPORT_GRACE = timedelta(minutes=30)
LIVE = (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS)

BOOKED_ACTION = "technical_round_booked"


# ---- the seam: the shape of Track I's TwoLinks ------------------------------------------------


@dataclass(frozen=True)
class TwoLinks:
    """What booking hands back. The field names are Track I's `TwoLinks`
    (shared/interview_contracts.py on their branch), so swapping in their
    contract at the merge changes no caller. No password: that is returned
    once, to HR, beside it, as Track I's contract requires."""

    interview_id: str
    meeting_id: str
    candidate_join_url: str
    #: Track I's live view. None until it exists; the screen says so.
    live_view_url: str | None
    duration_minutes: int
    expires_at: datetime | None


def _join_url(meeting_id: str) -> str:
    """The candidate's room (`/meet`, the console's `#/meet`). The meeting ID
    rides along so the page fills it in; the password never goes in a link."""
    return f"{(os.environ.get('APP_BASE_URL') or '').rstrip('/')}/meet?id={meeting_id}"


async def book_technical(session, candidate: Candidate, job: Job, minutes: int | None = None) -> TwoLinks:
    """Create the technical interview: a private room and the candidate's credentials."""
    settings = Settings.load()
    room = await daily.create_room(
        api_key=settings.daily_api_key, expiry_seconds=ROOM_TTL_HOURS * 3600, privacy="private"
    )
    expires = datetime.now(UTC) + timedelta(hours=ROOM_TTL_HOURS)
    interview = Interview(
        id=f"int_{uuid.uuid4().hex[:12]}",
        candidate_id=candidate.id,
        meeting_id=new_meeting_id(),
        password=new_password(),
        daily_room_name=room.name,
        daily_room_url=room.url,
        room_expires_at=expires,
        status=InterviewStatus.SCHEDULED,
        org_id=candidate.org_id,
        round="technical",
        # Stamped here to the microsecond: a candidate may have several MITRA rounds,
        # and the database's own default is whole seconds, so two could tie.
        created_at=datetime.now(UTC),
    )
    # What the bot runs to (bot/booked_time.py reads these): the plan's length,
    # trimmed on the realtime engine so the hard stop lands inside OpenAI's
    # 60-minute session (live/booking.py).
    timing = plan_timing(candidate, job, minutes)
    if timing is not None:
        interview.booked_duration_minutes = timing.booked_minutes
        interview.planned_duration_minutes = timing.planned_minutes
        interview.planned_grace_minutes = timing.grace_minutes
    session.add(interview)
    await session.flush()
    return TwoLinks(
        interview_id=interview.id, meeting_id=interview.meeting_id, candidate_join_url=_join_url(interview.meeting_id),
        live_view_url=None, expires_at=expires,
        duration_minutes=timing.booked_minutes if timing is not None else plan_minutes(candidate, job),
    )


# ---- MITRA's plan ------------------------------------------------------------------------------

#: Plans being built in this process, so a sweep never starts a second one.
_building: set[str] = set()


def _plan_builder():
    """The original flow's blueprint job (app.main). Imported late: app.main imports this package."""
    from app.main import generate_blueprint_task

    return generate_blueprint_task


async def prepare_plan(candidate_id: str) -> None:
    """Build MITRA's plan for the technical round, keeping the name HR gave.

    The generator also sets the candidate's name from the CV; on the hiring
    flow HR typed the name, and it stays the one everybody sees.
    """
    if candidate_id in _building:
        return
    _building.add(candidate_id)
    try:
        async with get_sessionmaker()() as session:
            candidate = await session.get(Candidate, candidate_id)
            if candidate is None:
                return
            name = candidate.name
        await _plan_builder()(candidate_id)
        async with get_sessionmaker()() as session:
            candidate = await session.get(Candidate, candidate_id)
            if candidate is not None and name and candidate.name != name:
                candidate.name = name
                await session.commit()
        await _prepare_for_interview(candidate_id)
    finally:
        _building.discard(candidate_id)


PREPARED_ACTION = "technical_plan_prepared"


#: The lengths HR can book (Track I: live/booking.py allows 20 to 60 minutes).
BOOKABLE_MINUTES = (20, 30, 40, 50, 60)


def plan_timing(candidate: Candidate, job: Job, requested: int | None = None):
    """The round's timing (Track I's live/booking.py): what is booked, what MITRA
    plans to, and the grace before the hard stop. The length is the plan's own;
    HR does not choose one. None when the plan's length cannot be booked (over
    60 minutes, from a job request made before the limit)."""
    from bot.config import interview_engine
    from live.booking import BookingError, booked_timing

    spec = (candidate.blueprint or {}).get("evaluation_spec") or {}
    try:
        grace = int(spec.get("overrun_grace_minutes", 5))
    except (TypeError, ValueError):
        grace = 5
    try:
        return booked_timing(plan_minutes=plan_minutes(candidate, job), grace_minutes=grace,
                             requested=requested, engine=interview_engine())
    except BookingError:
        return None


async def _prepare_for_interview(candidate_id: str) -> None:
    """Track I's plan preparation, once, right after the plan is built.

    `live.hr_hook.refine_plan_for_booking` adds the HR round's findings as
    neutral probe questions (Mitra never mentions HR), sizes the plan to the
    minutes it will run, and writes a goal for each question. Track I runs it
    at booking, where it can take up to three minutes; here it runs while the
    plan is still "being prepared", so booking stays instant and the plan HR
    books is the one MITRA uses. Run once per plan: a plan that already
    records what it did with HR findings, or has goals, is never refined again
    (refining twice would add the probes twice). Never fails the plan.
    """
    from live.hr_hook import refine_plan_for_booking

    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None or candidate.blueprint_status != BlueprintStatus.READY or not candidate.blueprint:
            return
        plan = candidate.blueprint
        if plan.get("hr_findings") or any(
            any(g for g in (c.get("question_goals") or [])) for c in plan.get("competency_plans") or []
        ):
            return
        job = await session.get(Job, candidate.job_id)
        timing = plan_timing(candidate, job) if job is not None else None
    # Not sized here: HR picks each booking's length, and the interviewer sizes the plan
    # to it when the round starts (bot/run_realtime.py), so the stored plan keeps every area.
    notes = await refine_plan_for_booking(candidate_id, booked_minutes=None)
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, candidate_id)
        session.add(AuditEvent(
            org_id=candidate.org_id if candidate else None, actor_user_id=None, action=PREPARED_ACTION,
            subject_type="candidate", subject_id=candidate_id,
            detail={"hr_findings_note": notes.hr_findings_note, "plan_note": notes.plan_note,
                    "timing_note": timing.note if timing is not None else None},
        ))
        await session.commit()


def plan_state(candidate: Candidate) -> str:
    if candidate.id in _building:
        return BlueprintStatus.GENERATING.value
    return str(candidate.blueprint_status or BlueprintStatus.PENDING)


def plan_minutes(candidate: Candidate, job: Job) -> int:
    """How long MITRA's plan runs: the plan's own duration, else the job's, else 40."""
    blueprint = candidate.blueprint or {}
    for source in (blueprint.get("evaluation_spec") or blueprint.get("spec") or {}, job.evaluation_spec or {}):
        try:
            minutes = int(source.get("duration_minutes"))
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            return minutes
    return DEFAULT_DURATION


# ---- helpers -----------------------------------------------------------------------------------


async def technical_report(session, candidate_id: str, interview_id: str | None = None) -> tuple[Interview, dict] | None:
    """The newest finished MITRA round with a report, or the one asked for."""
    query = select(Interview).where(
        Interview.candidate_id == candidate_id,
        Interview.round == "technical",
        Interview.feedback_report.isnot(None),
    )
    if interview_id:
        query = query.where(Interview.id == interview_id)
    row = (await session.execute(query.order_by(Interview.created_at.desc()))).scalars().first()
    return (row, row.feedback_report) if row is not None else None


async def technical_rounds(session, candidate_id: str) -> list[dict]:
    """Every MITRA round that produced a report, numbered from the first."""
    rows = (await session.execute(
        select(Interview).where(Interview.candidate_id == candidate_id, Interview.round == "technical",
                                Interview.feedback_report.isnot(None))
        .order_by(Interview.created_at)
    )).scalars().all()
    return [{"number": n, "interview_id": r.id, "ended_at": r.ended_at.isoformat() if r.ended_at else None,
             "recommendation": (r.feedback_report or {}).get("recommendation")}
            for n, r in enumerate(rows, start=1)]


async def _not_held_stage(session, candidate_id: str) -> S:
    """Where a booked round that did not happen leaves the candidate: back to "done" when
    an earlier round already has a report (a retake), else ready to be booked again."""
    return S.TECHNICAL_DONE if await technical_report(session, candidate_id) is not None else S.READY_FOR_TECHNICAL


RECOMMENDATION_WORDS = {
    "strong_evidence_for": "strong evidence for",
    "some_evidence_for": "some evidence for",
    "mixed": "mixed evidence",
    "limited_evidence": "limited evidence",
    "insufficient_signal": "not enough signal to say",
}


async def technical_recommendation(session, candidate_id: str) -> str | None:
    """MITRA's reading of the MITRA interview round, recorded beside the person's decision."""
    found = await technical_report(session, candidate_id)
    if found is None:
        return None
    signal = (found[1] or {}).get("recommendation")
    if not signal:
        return None
    return f"MITRA interview report: {RECOMMENDATION_WORDS.get(signal, signal)}."


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


async def latest_technical(session, candidate_id: str) -> Interview | None:
    return (
        await session.execute(
            select(Interview)
            .where(Interview.candidate_id == candidate_id, Interview.round == "technical")
            .order_by(Interview.created_at.desc(), Interview.id.desc())
        )
    ).scalars().first()


async def _booked_by(session, candidate_id: str) -> str | None:
    event = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == BOOKED_ACTION, AuditEvent.subject_id == candidate_id)
            .order_by(AuditEvent.id.desc())
        )
    ).scalars().first()
    if event is None or not event.actor_user_id:
        return None
    by = await session.get(User, event.actor_user_id)
    return by.name if by else None


async def _booking(session, user: User, candidate: Candidate, job: Job) -> dict | None:
    """The booked interview as the screen shows it. The password is for HR only."""
    interview = await latest_technical(session, candidate.id)
    if interview is None or interview.status not in LIVE:
        return None
    is_hr = Role(user.role) is Role.HR
    expires = interview.room_expires_at
    return {
        "interview_id": interview.id,
        "status": interview.status,
        "meeting_id": interview.meeting_id,
        "password": interview.password if is_hr else None,
        "candidate_join_url": _join_url(interview.meeting_id) if is_hr else None,
        "live_view_url": None,
        "duration_minutes": interview.booked_duration_minutes or plan_minutes(candidate, job),
        "expires_at": expires.isoformat() if expires else None,
        "started_at": interview.started_at.isoformat() if interview.started_at else None,
        "booked_by": await _booked_by(session, candidate.id),
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
    plan = plan_state(candidate)
    report = await technical_report(session, candidate.id)
    last = await latest_technical(session, candidate.id)
    return {
        "candidate": {"id": candidate.id, "name": candidate.name or "Unnamed candidate", "stage": candidate.stage},
        "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
        "plan": {
            "status": plan,
            "error": candidate.blueprint_error if plan == BlueprintStatus.FAILED.value else None,
            "minutes": plan_minutes(candidate, job),
            # What HR can book: Track I's range, 20 to 60 minutes (live/booking.py).
            "bookable_minutes": list(BOOKABLE_MINUTES),
        },
        "booking": await _booking(session, user, candidate, job) if stage is S.TECHNICAL_BOOKED else None,
        "last_round": {
            "status": last.status, "failure_reason": last.failure_reason,
            "feedback_status": last.feedback_status,
        } if last is not None else None,
        "round": await round_state(candidate, last) if stage in (S.TECHNICAL_BOOKED, S.TECHNICAL_DONE) else None,
        "has_report": report is not None,
        # Every MITRA round with a report; HR may book another (a retake of the same plan).
        "rounds": await technical_rounds(session, candidate.id),
        "sent_to_bu_head": await _sent(session, candidate.id),
        "can_book": is_hr and stage is S.READY_FOR_TECHNICAL and plan == BlueprintStatus.READY.value,
        "can_book_again": is_hr and stage is S.TECHNICAL_DONE and plan == BlueprintStatus.READY.value,
        "can_retry_plan": is_hr and stage is S.READY_FOR_TECHNICAL and plan in (BlueprintStatus.FAILED.value, BlueprintStatus.PENDING.value),
        "can_cancel": is_hr and stage is S.TECHNICAL_BOOKED and last is not None and last.status == InterviewStatus.SCHEDULED,
        "can_send": is_hr and stage is S.TECHNICAL_DONE,
    }


# ---- routes ------------------------------------------------------------------------------------


class BookRequest(BaseModel):
    #: The length HR books, 20 to 60 minutes (Track I's rule, live/booking.py). None:
    #: the plan's own length.
    duration_minutes: int | None = None


class CancelRequest(BaseModel):
    reason: str


class SendRequest(BaseModel):
    note: str | None = None


@router.get("/candidates/{candidate_id}/technical")
async def get_technical(candidate_id: str, user: User = Depends(current_user)) -> dict:
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if await settle(session, candidate):
            await session.commit()
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/technical/plan", status_code=202)
async def retry_plan(candidate_id: str, background: BackgroundTasks, user: User = Depends(_hr)) -> dict:
    """Build (or rebuild) MITRA's plan when it failed or never started."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) is not S.READY_FOR_TECHNICAL:
            raise HTTPException(status_code=409, detail="MITRA's plan is prepared for a candidate ready for the MITRA interview round.")
        if plan_state(candidate) in (BlueprintStatus.GENERATING.value, BlueprintStatus.READY.value):
            raise HTTPException(status_code=409, detail="MITRA's plan is already being prepared or is ready.")
        candidate.blueprint_status = BlueprintStatus.PENDING
        candidate.blueprint_error = None
        await session.commit()
    background.add_task(prepare_plan, candidate_id)
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/technical", status_code=201)
async def book(candidate_id: str, body: BookRequest | None = None, user: User = Depends(_hr)) -> dict:
    """Book the MITRA interview round: a private room and the candidate's credentials.

    HR picks the length (20 to 60 minutes; the plan's own length by default). The plan
    is sized to it when the round starts (bot/run_realtime.py)."""
    minutes = (body or BookRequest()).duration_minutes
    if minutes is not None:
        from live.booking import MAX_INTERVIEW_MINUTES, MIN_DURATION_MINUTES

        if not MIN_DURATION_MINUTES <= minutes <= MAX_INTERVIEW_MINUTES:
            raise HTTPException(status_code=422, detail=f"An interview can be {MIN_DURATION_MINUTES} to "
                                                        f"{MAX_INTERVIEW_MINUTES} minutes long; {minutes} was asked for.")
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) not in (S.READY_FOR_TECHNICAL, S.TECHNICAL_DONE):
            raise HTTPException(status_code=409, detail="Only a candidate ready for the MITRA interview round, or with a "
                                                        "finished one (a retake), can be booked.")
        if plan_state(candidate) != BlueprintStatus.READY.value:
            raise HTTPException(status_code=409, detail="MITRA's plan for this candidate is not ready yet. Book once it is.")
        last = await latest_technical(session, candidate.id)
        if last is not None and last.status in LIVE:
            raise HTTPException(status_code=409, detail="This candidate already has a MITRA interview round booked.")
        links = await book_technical(session, candidate, job, minutes)
        try:
            await move(session, candidate, S.TECHNICAL_BOOKED, by=user)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(
            session, BOOKED_ACTION, by=user, subject_type="candidate", subject_id=candidate.id,
            detail={"interview_id": links.interview_id, "duration_minutes": links.duration_minutes},
        )
        await session.commit()
        logger.info(f"[{links.interview_id}] MITRA interview round booked for {candidate.id} by {user.id}")
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/technical/cancel")
async def cancel(candidate_id: str, body: CancelRequest, user: User = Depends(_hr)) -> dict:
    """Cancel a booking nobody has joined. The candidate can be booked again; the reason is kept."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Say why the MITRA interview round is cancelled.")
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) is not S.TECHNICAL_BOOKED:
            raise HTTPException(status_code=409, detail="There is no booked MITRA interview round to cancel.")
        interview = await latest_technical(session, candidate.id)
        if interview is not None and interview.status == InterviewStatus.IN_PROGRESS:
            raise HTTPException(status_code=409, detail="This round has started, so it can no longer be cancelled.")
        if interview is not None and interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.EXPIRED
            interview.failure_reason = "Cancelled by HR."
        try:
            await move(session, candidate, await _not_held_stage(session, candidate.id), by=user, reason=reason)
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await audit(session, "technical_round_cancelled", by=user, subject_type="candidate", subject_id=candidate.id,
                    detail={"reason": reason, "interview_id": interview.id if interview else None})
        await session.commit()
        return await _view(session, user, candidate, job)


@router.post("/candidates/{candidate_id}/send-to-bu-head")
async def send_to_bu_head(candidate_id: str, body: SendRequest, user: User = Depends(_hr)) -> dict:
    """HR hands the finished result to the BU head, who decides. HR cannot reject here."""
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        if S(candidate.stage) is not S.TECHNICAL_DONE:
            raise HTTPException(status_code=409, detail="Only a candidate whose MITRA interview round is done can be sent to the BU head.")
        try:
            await move(
                session, candidate, S.WITH_BU_HEAD, by=user, reason=(body.note or "").strip() or None,
                model_recommendation=await technical_recommendation(session, candidate.id),
            )
        except StageError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        await session.commit()
        return await _view(session, user, candidate, job)


# ---- what happened: the background pass ------------------------------------------------------

_tasks: set[asyncio.Task] = set()


def start_plan(candidate_id: str) -> None:
    """Build the plan in the background, keeping a reference so the task is not collected."""
    task = asyncio.get_running_loop().create_task(prepare_plan(candidate_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settle(session, candidate: Candidate, now: datetime | None = None) -> bool:
    """Record how a booked round ended, for one candidate: True if they moved.

    Run by the sweep every 30 seconds, and also whenever the round is read
    (`get_technical`), so a report that is ready is never shown beside a stage
    that has not caught up: HR can send it on the moment it is there.
    """
    if S(candidate.stage) is not S.TECHNICAL_BOOKED:
        return False
    now = now or datetime.now(UTC)
    interview = await latest_technical(session, candidate.id)
    if interview is None:
        return False
    expires = _utc(interview.room_expires_at)
    ended = _utc(interview.ended_at)
    try:
        if interview.status == InterviewStatus.COMPLETED:
            scored = interview.feedback_status in (FeedbackStatus.READY, FeedbackStatus.FAILED)
            if scored or (ended is not None and now - ended > REPORT_GRACE):
                await move(session, candidate, S.TECHNICAL_DONE, by=None,
                           model_recommendation=await technical_recommendation(session, candidate.id))
                return True
        elif interview.status == InterviewStatus.NO_SHOW or (
            interview.status == InterviewStatus.SCHEDULED and expires is not None and expires < now
        ):
            # Track I's recording sweep may already have marked it `no_show`
            # (live/phase4.py); that status is kept, for its summaries. Either
            # way the candidate goes back to be booked.
            if interview.status == InterviewStatus.SCHEDULED:
                interview.status = InterviewStatus.EXPIRED
            await move(session, candidate, await _not_held_stage(session, candidate.id), by=None,
                       reason="The MITRA interview round's room expired before anyone joined. Book it again.")
            return True
        elif interview.status in (InterviewStatus.FAILED, InterviewStatus.EXPIRED):
            why = interview.failure_reason or interview.status
            await move(session, candidate, await _not_held_stage(session, candidate.id), by=None,
                       reason=f"The MITRA interview round did not take place ({why}). Book it again.")
            return True
    except StageError as exc:
        logger.error(f"[{candidate.id}] MITRA interview round could not move: {exc.message}")
    return False


async def sweep_once(now: datetime | None = None) -> int:
    """Start plans nobody started, and record how each booked round ended. Returns moves made."""
    now = now or datetime.now(UTC)
    moved = 0
    async with get_sessionmaker()() as session:
        waiting = list(
            await session.scalars(
                select(Candidate.id).where(
                    Candidate.stage == S.READY_FOR_TECHNICAL.value,
                    Candidate.blueprint_status == BlueprintStatus.PENDING,
                )
            )
        )
        booked = list(await session.scalars(select(Candidate).where(Candidate.stage == S.TECHNICAL_BOOKED.value)))
        for candidate in booked:
            if await settle(session, candidate, now):
                moved += 1
        await session.commit()
    for candidate_id in waiting:
        start_plan(candidate_id)
    return moved


# ---- the live room: what HR and the BU head see while MITRA interviews --------------------------

#: The browser's integrity facts, in neutral words: what happened, never why.
FOCUS_WORDS = {"hidden": "Left the interview tab", "blur": "Another window took focus"}


def _duration_words(seconds: float) -> str:
    s = max(0, round(seconds))
    if s < 60:
        return f"{s} second{'s' if s != 1 else ''}"
    m, rest = divmod(s, 60)
    return f"{m} min {rest} s" if rest else f"{m} min"


#: Track I's integrity events (proctoring/intake.py), one plain phrase each:
#: what the browser saw, never why, never a judgement.
INTEGRITY_WORDS = {
    "face_missing": "No face in view",
    "second_face": "A second person in view",
    "looking_away": "Looked away from the screen",
    "share_stopped": "Screen sharing stopped",
    "share_not_whole_screen": "A window was shared instead of the whole screen",
    "tab_hidden": "Left the interview tab",
    "tab_unfocused": "Another window took focus",
    "left_full_screen": "Left full screen",
    "paste": "Pasted into the interview page",
    "second_monitor": "A second screen was attached",
    "distance_out_of_band": "Sat too close to or too far from the camera",
    "second_voice": "A second voice was heard",
    "screen_content_flagged": "The shared screen was flagged for review",
}
#: Older interviews reported tab and window changes through /interviews/focus;
#: the same facts under the new kinds, so the two are counted together.
FOCUS_KIND = {"hidden": "tab_hidden", "blur": "tab_unfocused"}


def _integrity_label(row: dict) -> str:
    words = INTEGRITY_WORDS.get(row["kind"], "Noted by the browser")
    start, end = row.get("start_seconds"), row.get("end_seconds")
    repeats = int(row.get("repeats") or 1)
    if repeats > 1:
        text = f"{words}, {repeats} times within a minute"
    elif end is None:
        text = f"{words} (still the case when the call ended)"
    else:
        text = f"{words} for {_duration_words(max(0.0, end - (start or 0)))}"
    reminder = row.get("reminder")
    if reminder == "nudged" and row.get("reminder_index"):
        text += f"; reminder {row['reminder_index']} of 3 shown"
    elif reminder == "technical_prompt":
        text += ("; a warning was on screen until their face was back in view" if row["kind"] == "face_missing"
                 else "; asked to share the whole screen again")
    return text


async def integrity_facts_for(interview: Interview | None) -> dict:
    """What the candidate's browser reported during the MITRA round, newest first.

    Two sources: Track I's integrity events (camera, screen share, tab, focus,
    paste, second screen; posted by the room with its proctoring token, stored
    by proctoring/intake.py), and the older tab and focus reports. Facts for a
    person to read, never a score.
    """
    facts = integrity_facts(interview)
    if interview is None:
        return facts
    from proctoring.models import IntegrityEventRecord

    async with get_sessionmaker()() as session:
        rows = list(await session.scalars(
            select(IntegrityEventRecord).where(IntegrityEventRecord.interview_id == interview.id)))
    if not rows:
        return facts
    items = [{**e, "kind": FOCUS_KIND.get(e["kind"], e["kind"])} for e in facts["events"]]
    for r in rows:
        event = dict(r.event or {})
        start, end = r.start_seconds, r.end_seconds
        seconds = max(0.0, (end - start)) if end is not None else 0.0
        items.append({"at_seconds": start, "seconds": seconds, "kind": r.kind,
                      "label": _integrity_label({**event, "kind": r.kind, "start_seconds": start, "end_seconds": end})})
    items.sort(key=lambda e: e.get("at_seconds") or 0, reverse=True)
    counts: dict[str, dict] = {}
    for e in items:
        row = counts.setdefault(e["kind"], {"kind": e["kind"], "label": INTEGRITY_WORDS.get(e["kind"], e["kind"]),
                                            "count": 0, "total_seconds": 0.0})
        row["count"] += 1
        row["total_seconds"] += e["seconds"]
    return {"events": items, "counts": list(counts.values()), "total": len(items)}


def integrity_facts(interview: Interview | None) -> dict:
    """The candidate's browser reports, newest first, with counts. Facts only, never a score."""
    raw = list((interview.focus_events if interview is not None else None) or [])
    events = sorted(raw, key=lambda e: e.get("at_seconds") or 0, reverse=True)
    items = []
    counts: dict[str, dict] = {}
    for e in events:
        kind = e.get("kind") if e.get("kind") in FOCUS_WORDS else "hidden"
        seconds = float(e.get("seconds") or 0)
        items.append({
            "at_seconds": e.get("at_seconds"), "seconds": seconds, "kind": kind,
            "label": f"{FOCUS_WORDS[kind]} for {_duration_words(seconds)}",
        })
        row = counts.setdefault(kind, {"kind": kind, "label": FOCUS_WORDS[kind], "count": 0, "total_seconds": 0.0})
        row["count"] += 1
        row["total_seconds"] += seconds
    return {"events": items, "counts": list(counts.values()), "total": len(items)}


def plan_sections(candidate: Candidate) -> list[dict]:
    """MITRA's plan in the order it runs: opening, each competency, closing."""
    blueprint = candidate.blueprint or {}
    plans = blueprint.get("competency_plans") or []
    if not plans:
        return []
    sections = [{"key": "opening", "name": "Opening", "minutes": float(blueprint.get("opening_minutes") or 2)}]
    sections += [
        {"key": p.get("competency_id") or p.get("name"), "name": p.get("name") or "Competency",
         "minutes": float(p.get("time_budget_minutes") or 0)}
        for p in plans
    ]
    sections.append({"key": "closing", "name": "Closing", "minutes": float(blueprint.get("closing_minutes") or 2)})
    return sections


def current_section(sections: list[dict], elapsed_seconds: float) -> str | None:
    """Which section the plan expects by the clock. An estimate, not what MITRA is asking."""
    if not sections:
        return None
    clock = 0.0
    for section in sections:
        clock += section["minutes"] * 60
        if elapsed_seconds < clock:
            return section["key"]
    return sections[-1]["key"]


#: After joining, the candidate and Mitra take a few seconds to appear in the
#: room; an empty room then is a start, not an ending.
STARTING_GRACE = timedelta(seconds=45)
#: States in which the interview is still running in the room.
IN_THE_ROOM = ("live", "starting", "candidate_left", "closing")


def live_state(interview: Interview | None, presence: Presence | None = None, now: datetime | None = None) -> str:
    """Where the round actually stands.

    `in_progress` alone cannot tell a running interview from one the candidate
    has left: that takes Daily's presence (hiring/presence.py). With it:
    `live` (the candidate is in the call), `candidate_left` (Mitra is still
    there, waiting for them to rejoin before it closes), `closing` (nobody is
    left; Mitra is saving the interview). Then `scoring` while the report is
    compiled, and `done`.
    """
    if interview is None:
        return "not_booked"
    if interview.status == InterviewStatus.SCHEDULED:
        return "consented" if interview.consent_accepted_at else "waiting"
    if interview.status == InterviewStatus.IN_PROGRESS:
        if presence is None or not presence.known or presence.candidate:
            return "live"
        started = _utc(interview.started_at)
        if started is not None and (now or datetime.now(UTC)) - started < STARTING_GRACE:
            return "starting"
        return "candidate_left" if presence.mitra else "closing"
    if interview.status == InterviewStatus.COMPLETED:
        return "done" if interview.feedback_status in (FeedbackStatus.READY, FeedbackStatus.FAILED) else "scoring"
    return "did_not_happen"


async def round_state(candidate: Candidate, interview: Interview | None) -> dict:
    """The round's state with what it rests on, for the candidate page and the live room."""
    presence = None
    if interview is not None and interview.status == InterviewStatus.IN_PROGRESS:
        presence = await in_room(interview.daily_room_name, candidate.name)
    state = live_state(interview, presence)
    return {
        "state": state,
        "presence": presence.as_dict() if presence else None,
        # Within `scoring`: still saving the transcript, or compiling the report.
        "scoring_step": (
            ("compiling" if interview.feedback_status == FeedbackStatus.GENERATING else "saving")
            if interview is not None and state == "scoring" else None
        ),
        "report_failed": bool(interview is not None and interview.feedback_status == FeedbackStatus.FAILED),
        "ended_at": _utc(interview.ended_at).isoformat() if interview is not None and interview.ended_at else None,
    }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get("/candidates/{candidate_id}/technical/live")
async def technical_live(candidate_id: str, user: User = Depends(current_user)) -> dict:
    """The live picture of the technical round, for HR and the job's BU head.

    Nobody joins the call from here: the candidate is interviewed by MITRA
    alone. What shows is what is known without disturbing them: where the
    round stands, MITRA's plan by the clock, and the integrity facts the
    candidate's browser reports as they happen.
    """
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        candidate, job = await _candidate(session, user, candidate_id)
        interview = await latest_technical(session, candidate.id)
        current = await round_state(candidate, interview)
        state = current["state"]
        started = _utc(interview.started_at) if interview else None
        ended = _utc(interview.ended_at) if interview else None
        if started and state in IN_THE_ROOM:
            elapsed = (now - started).total_seconds()
        elif started and ended:
            elapsed = (ended - started).total_seconds()
        else:
            elapsed = 0.0
        sections = plan_sections(candidate)
        return {
            "candidate": {"id": candidate.id, "name": candidate.name or "Unnamed candidate", "stage": candidate.stage},
            "job": {"id": job.id, "title": job.title, "bu_name": job.business_unit},
            "state": state,
            "presence": current["presence"],
            "scoring_step": current["scoring_step"],
            "interview_id": interview.id if interview else None,
            "started_at": started.isoformat() if started else None,
            "ended_at": ended.isoformat() if ended else None,
            "elapsed_seconds": round(max(0.0, elapsed), 1),
            "planned_minutes": (interview.booked_duration_minutes if interview else None) or plan_minutes(candidate, job),
            "candidate_timezone": interview.candidate_timezone if interview else None,
            "recording_status": interview.recording_status if interview else None,
            "failure_reason": interview.failure_reason if interview and state == "did_not_happen" else None,
            "plan": {
                "sections": sections,
                "current": current_section(sections, elapsed) if state in IN_THE_ROOM else None,
            },
            "integrity": await integrity_facts_for(interview),
        }
