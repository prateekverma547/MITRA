"""The summary page's routes (Session D). Signed in, admin only (Track H's roles are a
merge fix). To be mounted by Session C: `app.include_router(analytics_summary_router)`
in app/main.py (one line); not mounted by this package.

GET /api/analytics/summary/jobs[?recent=N]            JobList: jobs with interviews, newest first
GET /api/analytics/summary/jobs/{job_id}?rows=true    JobSummary (with each interview)
GET /api/analytics/summary/interviews/{interview_id}  InterviewSummary
GET /api/analytics/summary/operations                 OperationsSummary (system-wide)
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from analytics.summary import build
from analytics.summary.shapes import InterviewSummary, JobList, JobSummary, OperationsSummary
from app.auth import require_admin
from app.db import get_sessionmaker

router = APIRouter(prefix="/api/analytics/summary", tags=["analytics"], dependencies=[Depends(require_admin)])


@router.get("/jobs", response_model=JobList)
async def jobs(recent: int = Query(default=0, ge=0, le=build.RECENT_MAX)) -> JobList:
    """The console home: jobs that have interviews, newest first; ?recent=N adds the N most
    recent interviews across all jobs."""
    async with get_sessionmaker()() as session:
        return await build.job_list(session, recent=recent)


@router.get("/jobs/{job_id}", response_model=JobSummary)
async def job(job_id: str, rows: bool = True) -> JobSummary:
    async with get_sessionmaker()() as session:
        out = await build.job_summary(session, job_id, include_rows=rows)
    if out is None:
        raise HTTPException(status_code=404, detail="No such job.")
    return out


@router.get("/interviews/{interview_id}", response_model=InterviewSummary)
async def interview(interview_id: str) -> InterviewSummary:
    async with get_sessionmaker()() as session:
        out = await build.interview_summary(session, interview_id)
    if out is None:
        raise HTTPException(status_code=404, detail="No such interview.")
    return out


@router.get("/operations", response_model=OperationsSummary)
async def operations() -> OperationsSummary:
    async with get_sessionmaker()() as session:
        return await build.operations_summary(session)
