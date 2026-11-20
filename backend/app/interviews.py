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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator
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
from bot.persistence import watch_bot_exit
from bot.presence import candidate_user_id
from bot.services.daily import create_meeting_token, create_room
from live.candidate_view import candidate_details
from proctoring.intake import issue_token as issue_intake_token
from shared.contracts import FeedbackReport
from shared.interview_contracts import (
    CandidateInterviewDetails,
    CandidateRefusal,
    CandidateRefusalReason,
    CleanedTranscript,
    EndReason,
    InterviewEnding,
    TwoLinks,
)

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
#: Strong references to the bot exit watchers, so they are not collected.
_exit_watchers: set = set()
BOT_TOKEN_MINUTES = 90


class InterviewCreated(BaseModel):
    interview_id: str
    candidate_id: str
    meeting_id: str
    #: Shown once, here, in its own field. Never in `links`, a log, an event or
    #: the stream (task 20). Stored as written: F-059, on hold until the pilot.
    password: str
    status: str
    expires_at: datetime
    #: Task 20: the two links and the timing. Added, so /admin (which reads
    #: only interview_id) is unaffected. links.duration_minutes = booked_minutes.
    links: TwoLinks | None = None
    #: What HR chose.
    booked_minutes: int | None = None
    #: What the interview runs to: on the realtime engine trimmed so it ends
    #: inside OpenAI's 60-minute session (live/booking.py).
    planned_minutes: int | None = None
    grace_minutes: int | None = None
    #: One plain sentence when the plan was trimmed; null when it was not.
    timing_note: str | None = None
    #: Wave 1 (C-124): what happened with the candidate's HR findings, in plain
    #: words ("No HR findings.", "Plan refined with HR findings: 1 of 2 probed.", or
    #: why they were not used).
    hr_findings_note: str | None = None
    #: C-A23: what was done to the plan for this booking, in plain words: the areas it
    #: was sized to (and those not planned for this length) and the goals written, or
    #: why the original plan was kept.
    plan_note: str | None = None


#: The one sentence for a duration that is not a whole number of minutes (C-170).
DURATION_NOT_A_NUMBER = "The interview length must be a whole number of minutes, from 20 to 60."


class BookingRequest(BaseModel):
    """Optional body. Without it the plan's duration is booked."""

    #: The range is checked in one place, live/booking.py booked_timing (20-60 on
    #: both engines), so every refusal says why in words.
    duration_minutes: int | None = None

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def _whole_minutes(cls, value):
        """A whole number of minutes, or nothing (the plan's own length). Refused with
        one plain sentence otherwise: before this, `true` was read as 1 and refused as
        "1 was asked for", and text or a fraction got a parser message (C-170)."""
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(DURATION_NOT_A_NUMBER)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        raise ValueError(DURATION_NOT_A_NUMBER)


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
    #: Lets this browser post its own integrity events for this one interview
    #: (POST /api/proctoring/interviews/{id}/events), and nothing else. Signed,
    #: purpose-bound, 90 minutes (proctoring/intake.py). Never logged.
    proctoring_token: str | None = None
    #: Wave 1 (shape only; issued once the rejoin route exists): a short-lived,
    #: purpose-bound pass the app holds IN MEMORY ONLY, exchanged after a fatal
    #: connection error for a fresh meeting token (RejoinExchangeRequest). Never
    #: the meeting password, never stored, never logged.
    rejoin_pass: str | None = None
    rejoin_pass_expires_at: datetime | None = None
    #: Phase 5 (PA-04): the real interview for the call screen (C-162). Additive.
    details: CandidateInterviewDetails | None = None


class RejoinExchangeRequest(BaseModel):
    """Wave 1 (shape only): the app, after a fatal connection error, swaps its
    rejoin pass for a fresh meeting token. Route models, not shared contracts:
    the shared contracts never hold a credential (decided 2026-11-19, C-115)."""

    interview_id: str
    rejoin_pass: str = Field(min_length=1, max_length=512)


class RejoinExchangeResponse(BaseModel):
    """Wave 1 (shape only): a fresh meeting token for the SAME interview. The
    return counts as a rejoin by the usual rule (a gap of 10 s or more); the
    pass is single-use and a new one comes with this answer."""

    interview_id: str
    room_url: str
    token: str
    rejoin_pass: str = Field(description="The next pass; the one sent is used up.")
    rejoin_pass_expires_at: datetime
    rejoins_counted: int = Field(ge=0)
    rejoins_left: int = Field(ge=0)


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
    #: The cleaned transcript beside the raw one (B-71). Always present: when
    #: none is stored it is `status: "missing"` with the reason in `reason`
    #: ("Not cleaned yet." or "Cleaning failed: ..."), never an error. Report
    #: quotes are verified against the raw `transcript`, never this.
    cleaned_transcript: CleanedTranscript
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
    #: C-A18 (C-169): how the interview ended, codes only, from the same builder the
    #: summary page reads (Session D's). None only if it cannot be built.
    ending: InterviewEnding | None = None


async def _ending(session, interview_id: str) -> InterviewEnding | None:
    """The summary's own ending and its ending decision for this interview (C-169).
    A report must load even when this cannot be built, so a failure is None, logged."""
    from analytics.summary.build import interview_summary

    try:
        summary = await interview_summary(session, interview_id)
    except Exception as exc:  # noqa: BLE001  (the report never depends on this)
        logger.warning(f"[{interview_id}] ending for the report not built: {exc!r}")
        return None
    if summary is None:
        return None
    decided = next((d for d in summary.decisions if d.phase == "ending"), None)
    return InterviewEnding(ending=summary.ending, decided=decided)


def _settings() -> Settings:
    return Settings.load()


NOT_CLEANED_YET = "Not cleaned yet."


def _cleaned_view(interview: Interview) -> CleanedTranscript:
    """The cleaned transcript for this view, or a "missing" one saying why.

    Read from `interviews.cleaned_transcript` (C-A6); until the cleaner writes
    that column it falls back to its stand-in, `session_metrics["cleaned_transcript"]`
    (C-84). Pre-interview turns are left out, as they are from the raw
    transcript in this view. Anything stored that does not read as a
    CleanedTranscript is reported as missing, never raised.
    """
    stored = interview.cleaned_transcript
    if stored is None:
        stored = (interview.session_metrics or {}).get("cleaned_transcript")
    if not stored:
        return CleanedTranscript(status="missing", reason=NOT_CLEANED_YET)
    try:
        cleaned = CleanedTranscript.model_validate(stored)
    except ValidationError:
        logger.warning(f"[{interview.id}] stored cleaned transcript does not read as the contract")
        return CleanedTranscript(status="missing", reason="The stored cleaned transcript could not be read.")
    if cleaned.status == "missing":
        why = cleaned.reason or "no reason recorded"
        return cleaned.model_copy(update={"reason": f"Cleaning failed: {why}"[:300]})
    return cleaned.model_copy(update={"turns": [t for t in cleaned.turns if not t.pre_interview]})


def _without_pre_interview(transcript: dict | None) -> dict | None:
    """The panel's view of the record: pre-interview audio left out.

    Since task 19 the stored record keeps audio heard before the interviewer
    first spoke, flagged `pre_interview` (it used to be dropped at save). The
    old admin panel shows every turn as the candidate's, and that audio may be
    someone else in the room, so it is removed from this view only. The stored
    record is not changed.
    """
    if not transcript or not isinstance(transcript.get("turns"), list):
        return transcript
    return {**transcript, "turns": [t for t in transcript["turns"] if not t.get("pre_interview")]}


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
async def create_interview(
    candidate_id: str, request: Request, body: BookingRequest | None = None
) -> InterviewCreated:
    """Book an interview for a candidate whose blueprint is ready.

    Task 20: the duration is set per booking (default: the plan's), 20-60
    minutes, trimmed on the realtime engine to end inside OpenAI's session
    limit, stored on the interview, and returned with the two links
    (live/booking.py).
    """
    from bot.config import interview_engine
    from live.booking import BookingError, base_url, booked_timing, two_links
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

        spec = (candidate.blueprint or {}).get("evaluation_spec") or {}
        try:
            timing = booked_timing(
                plan_minutes=int(spec.get("duration_minutes") or 40),
                grace_minutes=int(spec.get("overrun_grace_minutes", 5)),
                requested=body.duration_minutes if body else None,
                engine=interview_engine(),
            )
        except BookingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # C-A23: every plan is prepared for this booking by Session A's refine_for_booking:
    # HR findings (if any), sized to the minutes it will run, goals written. Never fails
    # the booking: on any failure or time-out the original plan is kept and the notes say so.
    from live.hr_hook import refine_plan_for_booking

    notes = await refine_plan_for_booking(candidate_id, booked_minutes=timing.planned_minutes)

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
            booked_duration_minutes=timing.booked_minutes,
            planned_duration_minutes=timing.planned_minutes,
            planned_grace_minutes=timing.grace_minutes,
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
        links=two_links(base=base_url(str(request.base_url)), interview_id=interview_id,
                        meeting_id=credentials[0], duration_minutes=timing.booked_minutes,
                        expires_at=expires_at),
        booked_minutes=timing.booked_minutes,
        planned_minutes=timing.planned_minutes,
        grace_minutes=timing.grace_minutes,
        timing_note=timing.note,
        hr_findings_note=notes.hr_findings_note,
        plan_note=notes.plan_note,
    )


#: One message for every credential failure. Distinguishing "no such meeting"
#: from "wrong password" would let someone probe for valid meeting ids.
INVALID_CREDENTIALS = "That meeting ID and password do not match an interview."
#: An interview marked failed (F-017: the bot died, or never started). Our
#: problem, not the candidate's, and it cannot be resumed: a new booking is
#: needed. 409 (Conflict), not 410: the interview did not "take place".
INTERRUPTED_DETAIL = ("This interview was interrupted by a problem on our side (our server restarted) and "
                      "can't continue. Please contact the person who invited you; they can arrange a new one.")
FAILED_DETAIL = (
    "This interview could not go ahead because of a problem on our side. Please "
    "contact the person who invited you; they can arrange a new one."
)
CAPACITY_DETAIL = (
    "All interview slots are busy right now. Your link is "
    "still valid. Please try again in a few minutes."
)
#: Never matches a real password (it is outside the alphabet); compared against
#: when the meeting ID is unknown, so both cases do the same work (C-75).
_NO_PASSWORD = "!" * 8


def _client(http_request: Request | None) -> str:
    return http_request.client.host if http_request is not None and http_request.client else "?"


RATE_LIMITED_DETAIL = "Too many attempts. Please wait a few minutes and try again."


def _refused(status: int, detail: str, reason: CandidateRefusalReason, *,
             retry_after: int | None = None) -> JSONResponse:
    """A refusal after the credentials matched (or a 429): the sentence as before, plus
    a stable code (C-163). Never used for a credential failure: that is the one 401."""
    body = CandidateRefusal(detail=detail, reason=reason, retry_after_seconds=retry_after)
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


def _rate_limited(address: str, meeting_id: str) -> JSONResponse | None:
    """The 429 when this address may not try this meeting ID now, with how long to wait
    (PA-15, C-165): the Retry-After header and `retry_after_seconds`. The wait depends
    only on this address's own attempts and failures, never on whether the interview
    exists."""
    from live.credential_limits import limits

    refused = limits.refusal(address, meeting_id)
    if refused is None:
        return None
    return _refused(429, RATE_LIMITED_DETAIL, CandidateRefusalReason.TOO_MANY_ATTEMPTS, retry_after=refused[1])


async def _find(session, meeting_id: str, password: str, address: str) -> Interview:
    """The interview these credentials open, or the one 401 for every failure."""
    from live.credential_limits import limits

    # Only the id and the password first, for a known and an unknown meeting ID
    # alike: loading the whole row (JSON columns and all) for a known ID made a
    # wrong password measurably slower than an unknown ID (+23 µs median,
    # permutation p = 0.001, C-75). The full row is read only once the
    # password has matched.
    found = (await session.execute(
        select(Interview.id, Interview.password).where(Interview.meeting_id == meeting_id)
    )).first() if meeting_id else None
    matched = passwords_match(password, found.password if found is not None else _NO_PASSWORD)
    if found is None or not matched:
        limits.failed(address, meeting_id or "?")
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)
    return await session.get(Interview, found.id)


TAKEN_PLACE_DETAIL = "This interview has already taken place."
BOT_NOT_STARTED_DETAIL = "The interviewer could not be started."


class BotStartFailed(Exception):
    """The bot process could not be spawned; the interview is marked failed (C-174)."""
EXPIRED_DETAIL = "This interview link has expired."


def _refusal(interview: Interview, now: datetime) -> tuple[JSONResponse | None, bool]:
    """(the answer for an interview that cannot be joined now, whether its room
    has expired). Shared by the check and the join, so both say the same thing.
    Writes nothing: the join records an expired room itself. Reached only after the
    credentials matched, so the state it reveals is the candidate's own (C-163)."""
    if interview.status == InterviewStatus.FAILED:
        from app.startup import was_interrupted

        if was_interrupted(interview):
            return _refused(409, INTERRUPTED_DETAIL, CandidateRefusalReason.INTERRUPTED_BY_RESTART), False
        return _refused(409, FAILED_DETAIL, CandidateRefusalReason.FAILED), False
    if interview.status == InterviewStatus.COMPLETED:
        return _refused(410, TAKEN_PLACE_DETAIL, CandidateRefusalReason.TAKEN_PLACE), False
    # EXPIRED is set only by the join, when the room expired before anyone joined
    # (below): the link expired, the interview never took place (PA-18).
    if interview.status in (InterviewStatus.EXPIRED, InterviewStatus.NO_SHOW):
        return _refused(410, EXPIRED_DETAIL, CandidateRefusalReason.EXPIRED), False
    room_expiry = interview.room_expires_at
    if room_expiry.tzinfo is None:
        room_expiry = room_expiry.replace(tzinfo=UTC)
    if room_expiry < now:
        return _refused(410, EXPIRED_DETAIL, CandidateRefusalReason.EXPIRED), True
    return None, False


class CredentialsCheck(BaseModel):
    meeting_id: str
    password: str


class CheckResponse(BaseModel):
    """The early check's answer when the candidate may join now."""

    may_join: bool = True
    #: "technical" or "hr". A stand-in on track_i, where there is no round
    #: column (merge fix: read Track H's `interviews.round`).
    round: str = "technical"
    #: "ready" (nobody has joined yet) or "in_progress" (a rejoin).
    state: str
    #: Phase 5 (PA-04): the real interview for the sign-in and consent screens
    #: (C-162). Only ever sent after the credentials matched. Additive.
    details: CandidateInterviewDetails | None = None


@router.post("/interviews/check", response_model=CheckResponse)
async def check_credentials(body: CredentialsCheck, http_request: Request) -> CheckResponse:
    """May the candidate join now? Read-only (Session B's request C-R1).

    The same codes and meanings as the join route: 401 wrong meeting ID or
    password (one answer for both), 409 the interview failed on our side, 410
    taken place or expired, 503 no free slot now, 429 too many attempts. It
    starts no bot, mints no Daily token, records no consent and changes no
    state. A convenience only: the join route checks everything again.
    """
    address = _client(http_request)
    meeting_id = normalise_meeting_id(body.meeting_id)
    if (limited := _rate_limited(address, meeting_id or "?")) is not None:
        return limited
    async with get_sessionmaker()() as session:
        interview = await _find(session, meeting_id, body.password, address)
        # Read only now, after the password matched (C-162).
        candidate = await session.get(Candidate, interview.candidate_id)
    refusal, _ = _refusal(interview, datetime.now(UTC))
    if refusal is not None:
        return refusal
    running = interview.status == InterviewStatus.IN_PROGRESS
    is_hr_round = getattr(interview, "round", None) == "hr"
    if running and not is_hr_round:
        # Wave 1: a reload into a running interview is admitted only while its
        # bot is alive here, and before the rejoin limit (the same rule as join).
        from live.rejoin import reload_refusal

        refused = await reload_refusal(interview.id)
        if refused is not None:
            status, reason, detail = refused
            return _refused(status, detail, CandidateRefusalReason(reason))  # one shape (C-171)
    if not running and not is_hr_round and not registry.has_capacity():
        return _refused(503, CAPACITY_DETAIL, CandidateRefusalReason.NO_FREE_SLOT)
    return CheckResponse(round=getattr(interview, "round", None) or "technical",
                         state="in_progress" if running else "ready",
                         details=None if is_hr_round else candidate_details(interview, candidate))


@router.post("/interviews/join", response_model=JoinResponse)
async def join_interview(request: JoinRequest, http_request: Request) -> JoinResponse:
    """Exchange meeting credentials for a short-lived Daily token.

    The bot is spawned here rather than at scheduling time, so it joins seconds
    after the candidate does instead of idling in an empty room.
    """
    address = _client(http_request)
    meeting_id = normalise_meeting_id(request.meeting_id)
    if (limited := _rate_limited(address, meeting_id or "?")) is not None:
        return limited
    if not meeting_id:
        from live.credential_limits import limits

        limits.failed(address, "?")
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)

    if not request.consent:
        raise HTTPException(
            status_code=400,
            detail="The interview cannot start until you accept the recording notice.",
        )

    async with get_sessionmaker()() as session:
        interview = await _find(session, meeting_id, request.password, address)

        refusal, room_expired = _refusal(interview, datetime.now(UTC))
        if room_expired:
            interview.status = InterviewStatus.EXPIRED
            await session.commit()
        if refusal is not None:
            return refusal

        candidate = await session.get(Candidate, interview.candidate_id)
        already_running = interview.status == InterviewStatus.IN_PROGRESS
        # An HR round is two people in a room: no bot, so no bot slot to claim
        # and nothing to spawn. The interview is simply under way once someone
        # is in it.
        is_hr_round = interview.round == "hr"
        if is_hr_round and interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)

        if already_running and not is_hr_round:
            # Wave 1: a candidate re-entering the credentials after a reload
            # rejoins the RUNNING interview. Admitted only while its bot is
            # alive here; never a second bot (the spawn below is skipped). The
            # bot sees a new participant and decides whether it counts.
            from live.rejoin import reload_refusal

            refused = await reload_refusal(interview.id)
            if refused is not None:
                status, reason, detail = refused
                logger.info(f"[{interview.id}] reload rejoin refused ({reason})")
                return _refused(status, detail, CandidateRefusalReason(reason))  # one shape (C-171)
            logger.info(f"[{interview.id}] candidate rejoining after a reload")

        # Checked before minting tokens or recording consent. Turning a
        # candidate away is bad; taking their consent and their Daily token and
        # then failing to seat them is worse.
        if not already_running and not is_hr_round:
            try:
                registry.claim()
            except AtCapacity as exc:
                logger.warning(f"[{interview.id}] join refused — {exc}")
                from live.phase4 import record_capacity

                await record_capacity("refused", interview.id)  # phase 4 data (D-41.2)
                return _refused(503, CAPACITY_DETAIL, CandidateRefusalReason.NO_FREE_SLOT)

        # Recorded against the interview it applies to, at the moment it was
        # given. First acceptance wins — a reconnect is not a fresh consent.
        if interview.consent_accepted_at is None:
            interview.consent_accepted_at = datetime.now(UTC)
        if request.timezone and not interview.candidate_timezone:
            interview.candidate_timezone = request.timezone[:64]
        await session.commit()
        interview_id = interview.id
        interview_round = interview.round
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
        # Marks this participant as the candidate, so watchers never drive the
        # interview (bot/presence.py).
        user_id=candidate_user_id(interview_id),
    )

    # A candidate refreshing the page must not start a second bot in the room,
    # and an HR round never gets one.
    if not already_running and not is_hr_round:
        try:
            await _start_bot(interview_id=interview_id, candidate_id=interview.candidate_id,
                            room_url=room_url, room_name=room_name, settings=settings,
                            timezone=request.timezone)
        except BotStartFailed:
            # The interview is already marked failed (failed_to_start); the same
            # refusal shape and code as every other (B-121, C-174).
            return _refused(500, BOT_NOT_STARTED_DETAIL, CandidateRefusalReason.FAILED_TO_START)
        from live.phase4 import record_capacity

        await record_capacity("started", interview_id)  # the live count at each start (D-41.2)

    # Wave 1: the pass the app keeps in memory to rejoin by itself after a
    # fatal connection error (live/rejoin.py). Issued after the bot started, so
    # its expiry follows the hard stop. Never the password; never logged.
    # An HR round has no bot to rejoin and nothing to proctor: no pass, no
    # proctoring token, no interview details.
    issued = None
    details = None
    if not is_hr_round:
        from live.rejoin import issue_pass_for

        issued = await issue_pass_for(interview_id)
        async with get_sessionmaker()() as session:  # started_at as the bot start set it
            details = candidate_details(await session.get(Interview, interview_id), candidate)
    return JoinResponse(
        interview_id=interview_id,
        room_url=room_url,
        token=candidate_token,
        candidate_name=candidate.name if candidate else None,
        role_title=role_title,
        round=interview_round,
        proctoring_token=None if is_hr_round else issue_intake_token(interview_id),
        rejoin_pass=issued.value if issued else None,
        rejoin_pass_expires_at=issued.expires_at if issued else None,
        details=details,
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
            # The meeting token goes on standard input, never on the command
            # line: any process on the machine can read a command line (`ps`,
            # /proc/<pid>/cmdline). A pipe is readable by the bot alone, never
            # touches the disk, and is gone once read (D-42).
            "--token-stdin",
            "--session-id", interview_id,
            "--interview-id", interview_id,
            "--blueprint-id", candidate_id,
            *(["--timezone", timezone] if timezone else []),
            cwd=str(BACKEND_DIR),
            env={**os.environ},
            stdin=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{interview_id}] could not spawn bot: {exc}")
        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is not None:
                interview.status = InterviewStatus.FAILED
                interview.failure_reason = f"Bot failed to start: {exc}"
                # Recorded the way the bot's own start-up failure is (P2-06):
                # ended_because beside the failed status, only into empty metrics.
                if not interview.session_metrics:
                    interview.session_metrics = {"ended_because": EndReason.FAILED_TO_START.value}
                await session.commit()
        raise BotStartFailed(BOT_NOT_STARTED_DETAIL) from exc

    registry.register(interview_id, process)
    # The bot's process id, so a restarted web process can tell whether it still
    # lives (app/startup.py).
    async with get_sessionmaker()() as session:
        row = await session.get(Interview, interview_id)
        if row is not None:
            row.bot_pid = process.pid
            await session.commit()
    # F-017: notice the bot exiting; an interview it left in_progress is failed.
    _exit_watchers.add(task := asyncio.create_task(
        watch_bot_exit(interview_id, process, on_exit=lambda: registry.release(interview_id))))
    task.add_done_callback(_exit_watchers.discard)
    logger.info(f"[{interview_id}] bot spawned")
    await _send_bot_token(interview_id, process, bot_token)


async def _send_bot_token(interview_id: str, process, bot_token: str) -> None:
    """Hand the bot its meeting token on standard input (D-42), after it is
    registered and watched. If the bot is already gone the write fails: that is
    logged (never the token), and the exit watcher fails the interview as for any
    bot that dies."""
    if process.stdin is None:
        logger.error(f"[{interview_id}] the bot has no standard input; it cannot be given its meeting token")
        return
    try:
        process.stdin.write(f"{bot_token}\n".encode())
        await process.stdin.drain()
        process.stdin.close()
    except (BrokenPipeError, ConnectionResetError) as exc:
        logger.error(f"[{interview_id}] the bot exited before it could be given its meeting token "
                     f"({type(exc).__name__})")


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
            transcript=_without_pre_interview(interview.transcript),
            cleaned_transcript=_cleaned_view(interview),
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
            ending=await _ending(session, interview.id),
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
