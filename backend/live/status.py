"""The latest live status reading per interview, held in memory, never stored.

The candidate's browser sends an `integrity_status` reading about once a
second (distance and whether it is in band, faces in view, page visible). The
live view's integrity tiles need the latest one; nobody needs the history, and
a per-second log of where somebody's face was is behavioural data this product
has no reason to keep. So readings live here, one per interview, replaced on
every update, and the relay (live/relay.py) merges them into the stream:

- a viewer receives each new reading as soon as it is published (the relay is
  woken, it does not wait for its next poll);
- a viewer who connects or reconnects receives the latest reading at once;
- status messages carry no SSE `id:`, so they never move a viewer's
  Last-Event-ID: resuming the stored stream is unaffected by them.

**One web process only.** The intake that publishes and the relay that reads
must be the same process. With more than one web server, a viewer connected
to another server would see no readings (C-35). Fine today (replicas pinned at
1); phase 5 needs routing by interview or a shared channel.
"""

import asyncio
import time
from dataclasses import dataclass

#: An interview not updated for this long is forgotten (the call is over).
FORGET_AFTER_SECONDS = 3600.0


@dataclass(frozen=True)
class Latest:
    version: int
    event: dict  # an InterviewEvent (kind integrity_status) as JSON
    published_at: float


class StatusBoard:
    def __init__(self) -> None:
        self._latest: dict[str, Latest] = {}
        #: interview -> {id of the event loop: event}. An asyncio.Event belongs
        #: to one loop; keying by loop keeps the board correct if it is ever
        #: read from more than one (tests do; the web process has one).
        self._changed: dict[str, dict[int, asyncio.Event]] = {}
        self._version = 0

    def publish(self, interview_id: str, event: dict, *, now: float | None = None) -> int:
        """Replace the interview's latest reading and wake its viewers. Returns its version."""
        now = time.monotonic() if now is None else now
        self._version += 1
        self._latest[interview_id] = Latest(self._version, event, now)
        for waiting in self._changed.pop(interview_id, {}).values():
            waiting.set()
        self._forget_stale(now)
        return self._version

    def latest(self, interview_id: str) -> Latest | None:
        return self._latest.get(interview_id)

    def snapshot(self) -> dict[str, Latest]:
        """Every interview's latest reading (a copy), for the drop detector."""
        return dict(self._latest)

    def waiter(self, interview_id: str) -> asyncio.Event:
        """An event set at the next publish for this interview. Take it *before*
        reading `latest`, so a publish in between is never missed."""
        per_loop = self._changed.setdefault(interview_id, {})
        key = id(asyncio.get_running_loop())
        event = per_loop.get(key)
        if event is None:
            event = per_loop[key] = asyncio.Event()
        return event

    def _forget_stale(self, now: float) -> None:
        stale = [k for k, v in self._latest.items() if now - v.published_at > FORGET_AFTER_SECONDS]
        for key in stale:
            self._latest.pop(key, None)
            self._changed.pop(key, None)

    def clear(self) -> None:
        self._latest.clear()
        self._changed.clear()


board = StatusBoard()
