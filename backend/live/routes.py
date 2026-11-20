"""Live view API: the event relay, evidence links, the recording, the interview
header and Check Intelligence data.

Auth is the old panel's `require_admin` for now. Merge fix (C_findings.md):
`current_user()` / `require_role()` / `can_see_job()` once Track H's sign-in
lands, so HR and the BU head of the job's BU can watch, per decision 7.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app.auth import require_admin
from app.db import Candidate, Interview, RecordingStatus, get_sessionmaker
from live import check_intel, evidence, relay
from live.store import all_events
from pydantic import BaseModel, Field

from app.interviews import RejoinExchangeRequest

from shared.interview_contracts import (
    CheckIntelReport,
    InterviewHeader,
    RecordingInfo,
    WatcherNote,
    WatcherNotesSection,
    WatchRefusal,
)

router = APIRouter(prefix="/api/live", tags=["live"])
ADMIN_ONLY = [Depends(require_admin)]

async def _interview(interview_id: str) -> Interview:
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="No such interview.")
    return interview


@router.get("/interviews/{interview_id}/events", dependencies=ADMIN_ONLY)
async def event_stream(
    interview_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None),
    after: str | None = Query(default=None, description="Resume point when no Last-Event-ID header can be sent."),
) -> StreamingResponse:
    """Server-sent events: every stored event for the interview, then new ones as they arrive.

    Each message's `id:` is the event's store sequence number. EventSource sends
    the last one back as `Last-Event-ID` when it reconnects, and the stream
    resumes after it, so a reconnecting viewer misses nothing.
    """
    try:
        after_seq = relay.parse_last_event_id(last_event_id, after)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be a whole number.") from exc
    await _interview(interview_id)
    return StreamingResponse(
        relay.stream(interview_id, after_seq, is_disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/interviews/{interview_id}/evidence-link", dependencies=ADMIN_ONLY)
async def evidence_link(interview_id: str, key: str = Query(max_length=256)) -> dict:
    """A short-lived link that opens one piece of evidence (key scheme: live/evidence.py)."""
    try:
        parsed = evidence.parse_key(key, for_interview=interview_id)
    except evidence.EvidenceKeyError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    interview = await _interview(interview_id)
    from app.recordings import stored_path

    path = stored_path(interview)
    if interview.recording_status != RecordingStatus.STORED or path is None or not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"There is no stored recording for this interview (it is '{interview.recording_status}').",
        )
    return {"key": key, **evidence.link_for(interview_id, parsed.seconds, interview.recording_offset_seconds)}


@router.get("/interviews/{interview_id}/recording", dependencies=ADMIN_ONLY,
            response_model=RecordingInfo)
async def recording_info(interview_id: str) -> RecordingInfo:
    """One interview's recording for the report: status, where to play it, and
    the offset for jumps (file_second = at_seconds - offset_seconds)."""
    from app.recordings import console_status, stored_path

    interview = await _interview(interview_id)
    path = stored_path(interview)
    stored = interview.recording_status == RecordingStatus.STORED and path is not None and path.exists()
    expires = interview.recording_expires_at
    if expires is not None and expires.tzinfo is None:
        from datetime import UTC

        expires = expires.replace(tzinfo=UTC)
    return RecordingInfo(
        interview_id=interview_id,
        status=console_status(interview, file_present=path is not None and path.exists()),
        media_url=f"/api/live/interviews/{interview_id}/recording/media" if stored else None,
        offset_seconds=interview.recording_offset_seconds,
        offset_known=interview.recording_offset_seconds is not None,
        bytes=interview.recording_bytes if stored else None,
        expires_at=expires if stored else None,
        error=interview.recording_error,
    )


@router.get("/interviews/{interview_id}/recording/media", dependencies=ADMIN_ONLY,
            include_in_schema=False)
async def recording_media(interview_id: str, request: Request) -> FileResponse:
    """The recording file, signed in. Range requests work (seeking); never cached
    by a shared proxy (`private, no-store`); every request is recorded in
    `recording_views` (who, which interview, when, which bytes). A plain
    `<video src>` sends the sign-in cookie, so no token is needed in the address.
    Merge fix: Track H's roles and can_see_job()."""
    from app.recordings import serve_recording

    return await serve_recording(interview_id, request, viewer="admin", route="console")


@router.post("/rejoin", include_in_schema=False)
async def rejoin_exchange(body: RejoinExchangeRequest, request: Request):
    """Wave 1: after a fatal connection error the candidate app swaps its rejoin
    pass for a fresh candidate meeting token for the SAME room. Public: the
    pass is the credential (single use, bound to the interview and its
    candidate). Refusals carry a reason code. Rate-limited like the join."""
    from app.interviews import CANDIDATE_TOKEN_MINUTES, RejoinExchangeResponse
    from bot.config import Settings
    from bot.presence import candidate_user_id
    from bot.services.daily import create_meeting_token
    from live import rejoin
    from live.credential_limits import limits
    from shared.contracts import REJOIN_LIMIT

    address = request.client.host if request.client else "?"
    key = f"rejoin:{body.interview_id}"
    if (refused := limits.refusal(address, key)) is not None:
        # The wait in seconds, as on the check and join routes (PA-15, C-165).
        return JSONResponse(status_code=429, content={"detail": "Too many attempts. Please wait a few minutes.",
                                                      "reason": "rate_limited", "retry_after_seconds": refused[1]},
                            headers={"Retry-After": str(refused[1])})
    try:
        interview, candidate = await rejoin.use_pass(body.interview_id, body.rejoin_pass)
    except rejoin.RejoinRefused as exc:
        if exc.reason == "invalid_pass":
            limits.failed(address, key)
        return JSONResponse(status_code=exc.status, content={"detail": str(exc), "reason": exc.reason})
    token = await create_meeting_token(
        api_key=Settings.load().daily_api_key, room_name=interview.daily_room_name,
        expiry_seconds=CANDIDATE_TOKEN_MINUTES * 60, is_owner=False,
        user_name=candidate.name if candidate else None, user_id=candidate_user_id(interview.id))
    following = await rejoin.issue_pass(interview)
    counted, _ = await rejoin.rejoin_counts(interview.id)
    return RejoinExchangeResponse(
        interview_id=interview.id, room_url=interview.daily_room_url, token=token,
        rejoin_pass=following.value, rejoin_pass_expires_at=following.expires_at,
        rejoins_counted=counted, rejoins_left=max(0, REJOIN_LIMIT - counted))


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/interviews/{interview_id}/notes", dependencies=ADMIN_ONLY, response_model=WatcherNote)
async def add_note(interview_id: str, body: NoteIn):
    """A watcher's note (wave 1). Stamped by the server with interview time and
    area; audited; a note_added event on the live stream; never sent to the bot.
    Merge fix: Track H's roles (HR and the job's BU head) instead of admin."""
    from live import notes

    try:
        return await notes.add_note(interview_id, author="watcher:admin", text=body.text)
    except notes.NoteRefused as exc:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc), "reason": exc.reason})


@router.get("/interviews/{interview_id}/notes", dependencies=ADMIN_ONLY, response_model=WatcherNotesSection)
async def list_notes(interview_id: str) -> WatcherNotesSection:
    """The report's "Watchers' notes" section: apart from the scored evidence."""
    from live import notes

    await _interview(interview_id)
    return await notes.notes_for(interview_id)


@router.get("/interviews/{interview_id}/header", dependencies=ADMIN_ONLY,
            response_model=InterviewHeader)
async def interview_header(interview_id: str) -> InterviewHeader:
    """Who and what an interview is, for the console's headings. Never the
    meeting password or the meeting ID (F-059). Merge fix: Track H's candidate
    record and interviews.round."""
    interview = await _interview(interview_id)
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, interview.candidate_id)
    return InterviewHeader(
        interview_id=interview.id,
        candidate_id=interview.candidate_id,
        candidate_name=(candidate.name or None) if candidate else None,
        round=getattr(interview, "round", None) or "technical",
        status=str(interview.status),
        booked_duration_minutes=interview.booked_duration_minutes,
    )


@router.post("/interviews/{interview_id}/watch", dependencies=ADMIN_ONLY)
async def watch(interview_id: str) -> dict:
    """Join a technical round live, hidden and receive-only (decision 7).

    Returns the room URL, a watcher meeting token and its expiry. Never the
    meeting password: the token is the pass. Every token issued is recorded
    before it is returned. Admin sign-in for now (merge fix: HR and the BU head
    of the job's BU, Track H's auth).
    """
    from bot.config import Settings
    from bot.services.daily import create_watcher_token
    from live.models import WatchRecord
    from live.watch import WatchRefused, watch_pass

    interview = await _interview(interview_id)
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, interview.candidate_id)
    who = "admin"  # the one shared admin; a user id after the merge
    try:
        granted = watch_pass(interview, candidate.blueprint if candidate else None, who=who)
    except WatchRefused as exc:
        # The message as before, plus a code the console can switch on (B-50).
        body = WatchRefusal(detail=str(exc), reason=exc.reason)
        return JSONResponse(status_code=exc.status, content=body.model_dump(mode="json"))
    token = await create_watcher_token(
        api_key=Settings.load().daily_api_key,
        room_name=interview.daily_room_name,
        user_id=granted.user_id,
        expires_at=int(granted.expires_at.timestamp()),
    )
    async with get_sessionmaker()() as session:
        session.add(WatchRecord(interview_id=interview_id, watcher=who,
                                daily_user_id=granted.user_id, token_expires_at=granted.expires_at))
        await session.commit()
    return {
        "interview_id": interview_id,
        "room_url": interview.daily_room_url,
        "token": token,
        "expires_at": granted.expires_at.isoformat(),
        "watcher_user_id": granted.user_id,
        "hidden": True,
        "can_send": False,
    }


@router.get("/interviews/{interview_id}/check-intel", dependencies=ADMIN_ONLY,
            response_model=CheckIntelReport, response_model_by_alias=True)
async def check_intelligence(interview_id: str) -> dict:
    """Check Intelligence data for one interview, live or after (read-only)."""
    interview = await _interview(interview_id)
    async with get_sessionmaker()() as session:
        candidate = await session.get(Candidate, interview.candidate_id)
    return check_intel.build(
        interview_id=interview.id,
        status=interview.status,
        started_at=interview.started_at,
        ended_at=interview.ended_at,
        transcript=interview.transcript,
        session_metrics=interview.session_metrics,
        blueprint=candidate.blueprint if candidate else None,
        events=await all_events(interview_id),
        cleaned_transcript=interview.cleaned_transcript,
    )


# -- security-rule violation reports (the /interview/ report-only policy) -------------------


@router.post("/csp-report", status_code=204, include_in_schema=False)
async def csp_report(request: Request) -> Response:
    """Where browsers send Content-Security-Policy violation reports.

    No sign-in: the browser sends these on its own. So it is size-capped and
    rate-limited, and logs only the violated rule and the blocked address (query
    string and fragment stripped), never the page, the referrer or a sample.
    Logs go to the web process's log (stderr; Railway's log stream when deployed).
    """
    from live import csp_reports

    if not csp_reports.limiter.allow(request.client.host if request.client else "?"):
        return Response(status_code=429)
    body = await request.body()
    if len(body) > csp_reports.MAX_BYTES:
        return Response(status_code=413)
    for directive, blocked in csp_reports.parse(body):
        csp_reports.record(directive, blocked)
    return Response(status_code=204)
