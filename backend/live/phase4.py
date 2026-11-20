"""Phase 4 data, recorded now so trends exist later (D-39 to D-41). Each writer
never raises: recording must not break a join or a booking.

- capacity_events: a row for every join refused for being full, and one for
  every bot start with the live bot count (peak load over time).
- no_show: booked interviews nobody joined by their room's expiry.
"""

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import update

from app.db import Interview, InterviewStatus, get_sessionmaker
from live.models import CapacityEventRecord


async def record_capacity(kind: str, interview_id: str | None) -> None:
    from app.capacity import max_concurrent, registry

    try:
        async with get_sessionmaker()() as session:
            session.add(CapacityEventRecord(kind=kind, interview_id=interview_id, live_bots=registry.live_count(),
                                            limit=max_concurrent()))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"capacity event not recorded ({kind}): {exc!r}")


async def mark_no_shows(now: datetime | None = None) -> int:
    """Booked, never joined, room expired: `no_show`. Returns how many."""
    now = now or datetime.now(UTC)
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(Interview)
            .where(Interview.status == InterviewStatus.SCHEDULED, Interview.started_at.is_(None),
                   Interview.room_expires_at < now)
            .values(status=InterviewStatus.NO_SHOW, ended_at=Interview.room_expires_at))
        await session.commit()
    if result.rowcount:
        logger.info(f"{result.rowcount} interview(s) marked no_show (booked, nobody joined, room expired)")
    return result.rowcount
