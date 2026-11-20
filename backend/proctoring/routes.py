"""Proctoring API: the browser's intake, and the integrity report for a reviewer."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import ValidationError

from app.auth import require_admin
from app.db import Interview, get_sessionmaker
from live.clock import interview_time_now
from proctoring import intake, report, status
from shared.interview_contracts import AfterCallResults, IntegrityReport, StoredIntegrityReport
from live.candidate_view import current_area_index

router = APIRouter(prefix="/api/proctoring", tags=["proctoring"])
#: A sync at the call screen and the odd retry: a few at once, then one every 2 s.
clock_limiter = status.TokenBucket(rate=0.5, burst=5)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


@router.post("/interviews/{interview_id}/events")
async def post_events(interview_id: str, request: Request) -> dict:
    """A batch of browser IntegrityEvents for one interview.

    Public in the route sweep's sense: the credential is the intake token from
    the join response, checked first, before anything else is read.
    """
    if not intake.token_valid_for(_bearer(request), interview_id):
        raise HTTPException(status_code=401, detail="Not allowed to report for this interview.")
    if not intake.limiter.allow(interview_id):
        raise HTTPException(status_code=429, detail="Too many batches; send fewer, larger ones.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > intake.MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Batch too large.")
    body = await request.body()
    if len(body) > intake.MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Batch too large.")
    try:
        batch = intake.IntakeBatch.model_validate(json.loads(body or b"{}"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Not a valid batch of integrity events.") from exc
    try:
        result = await intake.accept(interview_id, batch)
    except intake.IntakeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    if result["refused"]:
        logger.info(f"[{interview_id}] intake refused {len(result['refused'])} event(s)")
    return result


@router.post("/interviews/{interview_id}/status")
async def post_status(interview_id: str, request: Request) -> dict:
    """The browser's latest live reading, for the live view's tiles. Never stored.

    Same pass as the intake (token first, then the interview must be running),
    its own rate limit (about one a second, small burst), and a body that is
    exactly the integrity_status payload.
    """
    if not intake.token_valid_for(_bearer(request), interview_id):
        raise HTTPException(status_code=401, detail="Not allowed to report for this interview.")
    if not status.status_limiter.allow(interview_id):
        raise HTTPException(status_code=429, detail="Readings are taken about once a second.")
    body = await request.body()
    if len(body) > 1024:
        raise HTTPException(status_code=413, detail="A status reading is small.")
    try:
        reading = status.StatusBody.model_validate(json.loads(body or b"{}"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Not a valid integrity_status reading.") from exc
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        area = await current_area_index(session, interview) if interview is not None else None
    if interview is None:
        raise HTTPException(status_code=404, detail="No such interview.")
    try:
        intake.window_end(interview, datetime.now(UTC))
    except intake.IntakeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    version = status.publish(interview_id, reading, started_at=interview.started_at)
    # PA-04: the position of Mitra's current area in CandidateInterviewDetails.areas
    # (None before the first area change). Position only: candidate-safe (C-162).
    return {"accepted": True, "version": version, "clock": interview_time_now(interview),
            "current_area_index": area}


@router.get("/interviews/{interview_id}/clock")
async def get_clock(interview_id: str, request: Request) -> dict:
    """Interview time now, for the browser to sync when the call screen opens.

    Same pass as the intake (this interview's token, the interview running),
    with its own small rate limit. Returns `estimate: true` until the bot has
    stored its transcript zero (live/clock.py).
    """
    if not intake.token_valid_for(_bearer(request), interview_id):
        raise HTTPException(status_code=401, detail="Not allowed to read this interview's clock.")
    if not clock_limiter.allow(interview_id):
        raise HTTPException(status_code=429, detail="Sync once, then from each response.")
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        area = await current_area_index(session, interview) if interview is not None else None
    if interview is None:
        raise HTTPException(status_code=404, detail="No such interview.")
    try:
        intake.window_end(interview, datetime.now(UTC))
    except intake.IntakeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"clock": interview_time_now(interview), "current_area_index": area}


@router.get("/interviews/{interview_id}/after-call", dependencies=[Depends(require_admin)],
            response_model=AfterCallResults)
async def after_call(interview_id: str) -> AfterCallResults:
    """One interview's after-call status and results (Session D's runner, D-58).
    Signed in (admin for now; merge fix: Track H's roles). Signals for review,
    never a score."""
    from aftercall import settings as aftercall_settings

    async with get_sessionmaker()() as session:
        if await session.get(Interview, interview_id) is None:
            raise HTTPException(status_code=404, detail="No such interview.")
    status, signal, _ = report.after_call_results(interview_id)
    return AfterCallResults(interview_id=interview_id, enabled=aftercall_settings.enabled(), status=status,
                            second_voice=signal, paused=aftercall_settings.paused())


@router.get("/interviews/{interview_id}/report", dependencies=[Depends(require_admin)])
async def integrity_report(interview_id: str) -> IntegrityReport:
    """The IntegrityReport, assembled from what was stored. Admin auth for now (merge fix)."""
    async with get_sessionmaker()() as session:
        if await session.get(Interview, interview_id) is None:
            raise HTTPException(status_code=404, detail="No such interview.")
    return await report.assemble(interview_id)


@router.get("/interviews/{interview_id}/integrity", dependencies=[Depends(require_admin)],
            response_model=StoredIntegrityReport)
async def stored_integrity_report(interview_id: str) -> StoredIntegrityReport:
    """The STORED integrity report (D-93): written when the interview ends, rewritten when late
    events or after-call results arrive. Served as stored, never worked out here. Codes only;
    signals for review, never a score. Admin for now (merge fix: Track H's roles).
    404 detail `no_such_interview`, or `not_stored_yet` (running, never ran, or not written yet)."""
    from proctoring import stored

    async with get_sessionmaker()() as session:
        if await session.get(Interview, interview_id) is None:
            raise HTTPException(status_code=404, detail="no_such_interview")
    out = await stored.load(interview_id)
    if out is None:
        raise HTTPException(status_code=404, detail="not_stored_yet")
    return out
