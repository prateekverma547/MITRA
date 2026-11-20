"""Watchers' notes (phase 3, wave 1).

A watcher (admin today; HR and the job's BU head after the merge, Track H's
roles) types a note in the live view. The server, not the browser, stamps it:
the interview time from the server clock (live/clock.py) and the area current
at that moment (the last area_changed event stored at or before it). Each note
is a row (the audit: who, when) and a note_added event on the live stream.

**Nothing here reaches the bot.** The bot never reads the notes table or
note_added events, and this module imports nothing from bot/ and calls no
Daily API: a note cannot be sent into the call (tests/live/test_notes.py
guards it). Notes are people's observations, shown on the report apart from
the scored evidence (WatcherNotesSection.scored is always False).

Limits: 1 to 2000 characters (the contract's), NOTES_PER_MINUTE per author and
interview. Notes are taken while the interview runs and for
NOTES_AFTER_END_MINUTES after it ends, so a watcher can finish a thought.
"""

import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from app.db import Interview, InterviewStatus, get_sessionmaker
from live.clock import interview_time_now
from live.emit import make_event
from live.models import InterviewEventRecord, WatcherNoteRecord
from live.store import insert_events
from shared.interview_contracts import EventSource, NoteAdded, WatcherNote, WatcherNotesSection

NOTES_PER_MINUTE = 20
NOTES_AFTER_END_MINUTES = 15
MAX_NOTE_CHARS = 2000


class NoteRefused(Exception):
    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status, self.reason = status, reason


class _Limiter:
    def __init__(self, per_minute: int = NOTES_PER_MINUTE):
        self.per_minute = per_minute
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def allow(self, author: str, interview_id: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[(author, interview_id)]
        while hits and now - hits[0] > 60.0:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


limiter = _Limiter()


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _open_for_notes(interview: Interview, now: datetime) -> bool:
    if interview.status == InterviewStatus.IN_PROGRESS:
        return True
    ended = _aware(interview.ended_at)
    return (interview.status == InterviewStatus.COMPLETED and ended is not None
            and now - ended <= timedelta(minutes=NOTES_AFTER_END_MINUTES))


async def area_at(interview_id: str, at_seconds: float) -> str | None:
    """The area current at an interview time: the last stored area_changed at or before it."""
    async with get_sessionmaker()() as session:
        row = await session.scalar(
            select(InterviewEventRecord.payload)
            .where(InterviewEventRecord.interview_id == interview_id,
                   InterviewEventRecord.kind == "area_changed",
                   InterviewEventRecord.at_seconds <= at_seconds)
            .order_by(InterviewEventRecord.at_seconds.desc(), InterviewEventRecord.seq.desc())
            .limit(1))
    return (row or {}).get("payload", {}).get("area_id") if row else None


async def add_note(interview_id: str, *, author: str, text: str, now: datetime | None = None) -> WatcherNote:
    now = now or datetime.now(UTC)
    text = (text or "").strip()
    if not text:
        raise NoteRefused(422, "empty", "A note needs some text.")
    if len(text) > MAX_NOTE_CHARS:
        raise NoteRefused(422, "too_long", f"A note is at most {MAX_NOTE_CHARS} characters.")
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise NoteRefused(404, "no_interview", "No such interview.")
        if not _open_for_notes(interview, now):
            raise NoteRefused(409, "not_running", "Notes are taken while the interview runs.")
        clock = interview_time_now(interview, now)
    if clock["interview_seconds"] is None:
        raise NoteRefused(409, "not_running", "Notes are taken while the interview runs.")
    if not limiter.allow(author, interview_id):
        raise NoteRefused(429, "rate_limited", f"At most {NOTES_PER_MINUTE} notes a minute.")
    at = clock["interview_seconds"]
    area = await area_at(interview_id, at)
    note_id = f"note_{secrets.token_hex(8)}"
    note = WatcherNote(note_id=note_id, interview_id=interview_id, author=author, text=text, at_seconds=at,
                       area_id=area, created_at=now)
    async with get_sessionmaker()() as session:
        session.add(WatcherNoteRecord(note_id=note_id, interview_id=interview_id, author=author, text=text,
                                      at_seconds=at, at_estimate=clock["estimate"], area_id=area, created_at=now))
        await session.commit()
    await insert_events([make_event(interview_id, NoteAdded(note_id=note_id, author=author, text=text, area_id=area),
                                    source=EventSource.SERVER, at_seconds=at, event_id=f"{interview_id}:{note_id}")])
    # The audit line: who, which interview, when, how long. Never the text.
    logger.info(f"[{interview_id}] note {note_id} added by {author} at {at:.1f}s "
                f"({'estimated ' if clock['estimate'] else ''}interview time), {len(text)} characters")
    return note


async def notes_for(interview_id: str) -> WatcherNotesSection:
    async with get_sessionmaker()() as session:
        rows = list(await session.scalars(
            select(WatcherNoteRecord).where(WatcherNoteRecord.interview_id == interview_id)
            .order_by(WatcherNoteRecord.at_seconds, WatcherNoteRecord.created_at)))
    return WatcherNotesSection(interview_id=interview_id, notes=[
        WatcherNote(note_id=r.note_id, interview_id=r.interview_id, author=r.author, text=r.text,
                    at_seconds=r.at_seconds, area_id=r.area_id, created_at=_aware(r.created_at)) for r in rows])
