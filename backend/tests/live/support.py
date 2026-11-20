"""Shared setup for the live and proctoring tests: a throwaway database and rows."""

from datetime import UTC, datetime, timedelta

from shared.interview_contracts import (
    EventSource,
    InterviewEnded,
    TimeNoteSent,
    TranscriptTurnEvent,
)

ADMIN_PASSWORD = "test-admin-pw"


async def fresh_db(tmp_path, monkeypatch, name: str = "live.db"):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/{name}")
    from app import db

    await db.reset_engine()
    await db.create_all()
    return db


async def seed_interview(
    interview_id: str = "int_live00000001",
    *,
    candidate_id: str = "cand_live1",
    status: str = "in_progress",
    started_ago: float = 600.0,
    ended_ago: float | None = None,
    **fields,
) -> str:
    from app import db

    now = datetime.now(UTC)
    async with db.get_sessionmaker()() as session:
        if await session.get(db.Candidate, candidate_id) is None:
            if await session.get(db.Job, "job_live") is None:
                session.add(db.Job(id="job_live", jd_text="jd", spec_status=db.SpecStatus.READY))
                await session.flush()
            session.add(db.Candidate(id=candidate_id, job_id="job_live", name="Test Candidate",
                                     cv_text="cv", blueprint_status=db.BlueprintStatus.READY,
                                     blueprint=fields.pop("blueprint", None)))
        session.add(db.Interview(
            id=interview_id,
            candidate_id=candidate_id,
            meeting_id=fields.pop("meeting_id", interview_id[-9:]),
            password=fields.pop("password", "pw"),
            daily_room_name=f"room-{interview_id}",
            daily_room_url=f"https://example.daily.co/room-{interview_id}",
            room_expires_at=now + timedelta(hours=24),
            status=status,
            started_at=now - timedelta(seconds=started_ago) if started_ago is not None else None,
            ended_at=now - timedelta(seconds=ended_ago) if ended_ago is not None else None,
            **fields,
        ))
        await session.commit()
    return interview_id


def turn_event(interview_id: str, i: int, *, at: float | None = None):
    from live.emit import make_event

    return make_event(
        interview_id,
        TranscriptTurnEvent(turn_index=i, speaker="interviewer" if i % 2 == 0 else "candidate",
                            text=f"turn {i}"),
        source=EventSource.BOT, at_seconds=at if at is not None else float(i),
        event_id=f"{interview_id}:t{i:05d}",
    )


def note_event(interview_id: str, i: int):
    from live.emit import make_event

    return make_event(interview_id, TimeNoteSent(text=f"note {i}"), source=EventSource.COORDINATOR,
                      at_seconds=float(i), event_id=f"{interview_id}:n{i:05d}")


def ended_event(interview_id: str, at: float = 100.0):
    from live.emit import make_event

    return make_event(interview_id, InterviewEnded(ended_because="interview_complete",
                                                   duration_seconds=at),
                      source=EventSource.BOT, at_seconds=at, event_id=f"{interview_id}:end")
