"""Development helpers for the live view: a demo interview row to emit against.

Used by scripts/fake_bot.py and scripts/replay_events.py. Refuses to run on a
deployed instance, and writes only rows it names itself.
"""

from datetime import UTC, datetime, timedelta

from app import db
from app.meeting import new_meeting_id

DEMO_JOB_ID = "job_live_demo"
DEMO_CANDIDATE_ID = "cand_live_demo"


class RefusedOnDeployed(RuntimeError):
    pass


async def ensure_demo_interview(interview_id: str) -> str:
    """Create (or reuse) an in-progress interview row for local live-view work."""
    if db.is_deployed():
        raise RefusedOnDeployed("Refusing: this is a deployed instance.")
    await db.create_all()
    now = datetime.now(UTC)
    async with db.get_sessionmaker()() as session:
        if await session.get(db.Interview, interview_id) is not None:
            return interview_id
        if await session.get(db.Job, DEMO_JOB_ID) is None:
            session.add(db.Job(id=DEMO_JOB_ID, title="Live view demo", jd_text="(demo)",
                               spec_status=db.SpecStatus.READY))
            await session.flush()
        if await session.get(db.Candidate, DEMO_CANDIDATE_ID) is None:
            session.add(db.Candidate(id=DEMO_CANDIDATE_ID, job_id=DEMO_JOB_ID, name="Demo Candidate",
                                     cv_text="(demo)", blueprint_status=db.BlueprintStatus.READY))
            await session.flush()
        session.add(db.Interview(
            id=interview_id, candidate_id=DEMO_CANDIDATE_ID,
            meeting_id=new_meeting_id(),
            password="demo", daily_room_name=f"demo-{interview_id}",
            daily_room_url=f"https://example.invalid/{interview_id}",
            room_expires_at=now + timedelta(hours=24),
            status=db.InterviewStatus.IN_PROGRESS, started_at=now,
        ))
        await session.commit()
    return interview_id
