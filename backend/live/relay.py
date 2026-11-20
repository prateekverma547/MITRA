"""The relay: stored events for one interview, streamed as server-sent events.

The relay reads the store, it is not handed events in memory. That is what lets
a bot in another process (or, from phase 5, on another machine) feed it, and
what lets a viewer reconnect after a web restart and miss nothing: the SSE
`id:` of every event is its store `seq`, and the browser's EventSource sends the
last one it saw back as `Last-Event-ID` when it reconnects.

Latency budget: the writer stores within one write of `emit` (no batching
delay), and the relay polls every `POLL_SECONDS`, so an event reaches a viewer
in at most about one poll interval plus one write. Measured on the real route:
track_i_notes/C_findings.md.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from sqlalchemy import select

from app.db import get_sessionmaker
from live.models import InterviewEventRecord
from live.status import StatusBoard, board
from live.store import events_after

POLL_SECONDS = 0.5
#: The longest the stream stays silent: after this long with nothing sent, a
#: `ping` event goes out. A real event, not an SSE comment, because EventSource
#: never surfaces comments, so a page could not tell a quiet stream from a dead
#: one (B-51). No `id:` line, so it never moves the viewer's resume point.
HEARTBEAT_SECONDS = 15.0
PING = "event: ping\ndata: {}\n\n"
#: For the viewer: with a message at least every HEARTBEAT_SECONDS (plus one
#: poll), a stream silent for this long is dead and should be reconnected. Two
#: missed pings plus margin for the network (C_findings, round 2).
DEAD_AFTER_SECONDS = 35.0
#: After the interview has ended and nothing new has arrived for this long, the
#: stream sends `event: end` and closes, so the viewer stops reconnecting.
END_GRACE_SECONDS = 2.0
#: Tells EventSource how soon to reconnect after a drop.
RECONNECT_MS = 1000


def sse_message(seq: int, payload: dict) -> str:
    # JSON never contains a raw newline, so one `data:` line is always enough.
    return f"id: {seq}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def sse_status(payload: dict) -> str:
    """A live status reading. No `id:` line on purpose: per the SSE spec a
    message without one leaves the viewer's last event id unchanged, so status
    never moves the resume point of the stored stream (live/status.py)."""
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def _shielded(coroutine):
    """Run a database read so a viewer disconnecting cannot cut it in half.

    When a viewer's connection dies, the server cancels the streaming task. A
    cancel landing inside a database read made SQLAlchemy kill the connection
    mid-transaction, and the session's close then failed ("no active
    connection", B-83). Shielded, the read finishes and closes its session
    normally; the cancel still ends the stream right after. A failure of the
    read after the stream is gone is consumed, not left unretrieved.
    """
    task = asyncio.ensure_future(coroutine)
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return await asyncio.shield(task)


async def _has_ended(interview_id: str) -> bool:
    async with get_sessionmaker()() as session:
        found = await session.scalar(
            select(InterviewEventRecord.seq)
            .where(
                InterviewEventRecord.interview_id == interview_id,
                InterviewEventRecord.kind == "interview_ended",
            )
            .limit(1)
        )
    return found is not None


async def stream(
    interview_id: str,
    after_seq: int,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_seconds: float = POLL_SECONDS,
    status_board: StatusBoard | None = None,
) -> AsyncIterator[str]:
    """Yield SSE text for every event after `after_seq`, then follow new ones.

    The latest live status reading (live/status.py) is sent first, at once, and
    then every newer one as soon as it is published.
    """
    loop = asyncio.get_running_loop()
    statuses = status_board or board
    yield f"retry: {RECONNECT_MS}\n\n"
    cursor = after_seq
    status_seen = 0
    ended_since: float | None = None
    last_sent = loop.time()
    if await _shielded(_has_ended(interview_id)):
        ended_since = loop.time()
    while True:
        if await is_disconnected():
            return
        woken = statuses.waiter(interview_id)  # before reading, so no publish is missed
        latest = statuses.latest(interview_id)
        if latest is not None and latest.version > status_seen:
            status_seen = latest.version
            last_sent = loop.time()
            yield sse_status(latest.event)
        rows = await _shielded(events_after(interview_id, cursor))
        for seq, payload in rows:
            yield sse_message(seq, payload)
            cursor = seq
            if payload.get("kind") == "interview_ended":
                ended_since = loop.time()
        if rows:
            last_sent = loop.time()
            if ended_since is not None:
                ended_since = loop.time()
            continue  # there may be more than one page waiting
        now = loop.time()
        if ended_since is not None and now - ended_since >= END_GRACE_SECONDS:
            yield "event: end\ndata: {}\n\n"
            return
        if now - last_sent >= HEARTBEAT_SECONDS:
            yield PING
            last_sent = now
        # Wait for the next poll, or less if a status reading is published.
        try:
            await asyncio.wait_for(woken.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


def parse_last_event_id(header: str | None, query: str | None) -> int:
    """The resume point. The header (sent by EventSource on reconnect) wins.

    Raises ValueError on anything that is not a non-negative integer, so the
    route can answer 400 instead of silently replaying from the start.
    """
    raw = header if header not in (None, "") else query
    if raw in (None, ""):
        return 0
    value = int(raw)
    if value < 0:
        raise ValueError("negative")
    return value
