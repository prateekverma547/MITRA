"""The status-reading drop detector (phase 3, wave 1; D-30, D-31).

The candidate's browser sends a status reading at most once a second
(frontend/candidate-app httpSink.ts STATUS_MIN_MS) to the web process, where
live/status.py keeps the latest per interview. Healthy gaps measured at most
1.29 s (D-30). Three missed readings, DROP_AFTER_SECONDS without one, is a
drop: noticed about 2-3 s after the connection dies, against Daily's 34-38 s.

On a drop the detector writes:
- a `candidate_dropped` event (detected_by status_readings, source server) to
  the event store, so the live view shows it;
- a "dropped" connection signal, for the bot.
When readings start again it writes a "resumed" signal with the gap. It never
writes `candidate_returned`: whether a return counts, the rejoin number, the
grace window and the ending are the bot's to decide (it also sees Daily's own
events). The bot reads the signals cheaply: `signals_after(interview_id,
after_seq)`, one indexed query, polled every second or so (C_findings, C-121).

Only for interviews in progress: readings stop at the end of every interview,
and that is not a drop. One web process (as live/status.py: the board is per
process). DROP_DETECTOR_DISABLED switches the loop off (tests drive `tick`).
"""

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from app.db import Interview, InterviewStatus, get_sessionmaker
from live.clock import interview_time_now
from live.emit import make_event
from live.models import ConnectionSignalRecord
from live.status import StatusBoard, board
from live.store import insert_events
from shared.contracts import DropDetection
from shared.interview_contracts import CandidateDropped, EventSource

#: Three readings missed at one a second.
DROP_AFTER_SECONDS = 3.0
TICK_SECONDS = 0.5
DISABLED_ENV = "DROP_DETECTOR_DISABLED"


@dataclass(frozen=True)
class Signal:
    seq: int
    kind: str          # "dropped" | "resumed"
    detected_by: str
    at_seconds: float
    gap_seconds: float | None


@dataclass
class _Dropped:
    last_reading: float       # monotonic time of the last reading before the gap
    version: int              # the board version of that reading


class DropDetector:
    def __init__(self, status_board: StatusBoard | None = None):
        self.board = status_board or board
        self.dropped: dict[str, _Dropped] = {}
        #: Interviews found not running at a gap, by the reading version seen
        #: then: not asked about again until a new reading arrives (an ended
        #: interview's readings stop for good, and the board keeps them an hour).
        self.not_running: dict[str, int] = {}

    async def tick(self, now: float | None = None) -> list[tuple[str, str]]:
        """One look at every interview with readings. Returns what was written."""
        now = time.monotonic() if now is None else now
        written = []
        for interview_id, latest in self.board.snapshot().items():
            state = self.dropped.get(interview_id)
            if state is None:
                if self.not_running.get(interview_id) == latest.version:
                    continue
                if now - latest.published_at >= DROP_AFTER_SECONDS:
                    if await self._record_drop(interview_id, latest.published_at, now):
                        self.dropped[interview_id] = _Dropped(latest.published_at, latest.version)
                        written.append((interview_id, "dropped"))
                    else:
                        self.not_running[interview_id] = latest.version
            elif latest.version != state.version:
                gap = latest.published_at - state.last_reading
                await self._record_resume(interview_id, latest.published_at, gap, now)
                del self.dropped[interview_id]
                written.append((interview_id, "resumed"))
        return written

    async def _interview_seconds(self, interview_id: str, monotonic_at: float, now: float) -> float | None:
        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is None or interview.status != InterviewStatus.IN_PROGRESS:
                return None
            wall = datetime.now(UTC) - timedelta(seconds=now - monotonic_at)
            return interview_time_now(interview, wall)["interview_seconds"]

    async def _record_drop(self, interview_id: str, last_reading: float, now: float) -> bool:
        at = await self._interview_seconds(interview_id, last_reading, now)
        if at is None:
            return False  # not in progress (ended, or never started): not a drop
        event = make_event(interview_id, CandidateDropped(detected_by=DropDetection.STATUS_READINGS),
                           source=EventSource.SERVER, at_seconds=at)
        await insert_events([event])
        await _add_signal(interview_id, "dropped", at, None)
        logger.warning(f"[{interview_id}] candidate's status readings stopped: dropped at {at:.1f}s "
                       f"(noticed {now - last_reading:.1f}s after the last reading)")
        return True

    async def _record_resume(self, interview_id: str, first_reading: float, gap: float, now: float) -> None:
        at = await self._interview_seconds(interview_id, first_reading, now)
        if at is None:
            return
        await _add_signal(interview_id, "resumed", at, round(gap, 2))
        logger.info(f"[{interview_id}] candidate's status readings resumed at {at:.1f}s after {gap:.1f}s")


async def _add_signal(interview_id: str, kind: str, at: float, gap: float | None) -> None:
    async with get_sessionmaker()() as session:
        session.add(ConnectionSignalRecord(interview_id=interview_id, kind=kind,
                                           detected_by=DropDetection.STATUS_READINGS.value,
                                           at_seconds=at, gap_seconds=gap))
        await session.commit()


async def signals_after(interview_id: str, after_seq: int = 0) -> list[Signal]:
    """For the bot: the connection signals after `after_seq`, oldest first.
    One indexed query; poll it every second or so and keep the last seq."""
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(ConnectionSignalRecord)
            .where(ConnectionSignalRecord.interview_id == interview_id, ConnectionSignalRecord.seq > after_seq)
            .order_by(ConnectionSignalRecord.seq))
        return [Signal(r.seq, r.kind, r.detected_by, r.at_seconds, r.gap_seconds) for r in rows]


detector = DropDetector()


def enabled() -> bool:
    return os.environ.get(DISABLED_ENV, "").strip().lower() not in {"1", "true", "yes", "on"}


async def watch_forever() -> None:
    """The background loop, started by the app's lifespan."""
    while True:
        try:
            await detector.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"drop detector tick failed, will run again: {exc!r}")
        await asyncio.sleep(TICK_SECONDS)
