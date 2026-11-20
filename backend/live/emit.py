"""`emit()`: report something that happened in an interview, without waiting.

The contract (docs/DEV_PLAN.md section 5): **emission never blocks the call.**
`emit` puts the event on a bounded in-memory queue with `put_nowait` and
returns. If the queue is full the event is dropped and counted; nothing waits
for room. A background writer drains the queue into the store in batches, and
when the store is slow or down it is the writer that waits and retries, never
the caller.

How a process uses it (the bot's wiring is a later joint step; this is the
whole of it):

    from live.emit import EventEmitter, make_event
    from shared.interview_contracts import EventSource, TranscriptTurnEvent

    emitter = EventEmitter(name=interview_id)
    await emitter.start()
    ...
    emitter.emit(make_event(interview_id, TranscriptTurnEvent(...),
                            source=EventSource.BOT, at_seconds=12.4))
    ...
    stats = await emitter.close()       # flushes what it can in 2 s
    session_metrics["events"] = stats    # drops are reported, never hidden

Call `emit` from the event loop's own thread (Pipecat observers and the
coordinator already run there).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from loguru import logger
from pydantic import BaseModel, ValidationError

from shared.interview_contracts import EventKind, EventSource, InterviewEvent

#: Enough for minutes of a store outage at interview event rates (a few per
#: second), small enough to be a few hundred kilobytes at most.
DEFAULT_CAPACITY = 2000
#: The most events in one INSERT.
DEFAULT_BATCH_SIZE = 100
#: Waits between retries while the store is failing, doubling to the cap.
RETRY_FIRST_SECONDS = 0.25
RETRY_MAX_SECONDS = 5.0

StoreFn = Callable[[Sequence[InterviewEvent]], Awaitable[tuple[list[str], list[str]]]]


def new_event_id(interview_id: str) -> str:
    return f"{interview_id}:{uuid.uuid4().hex}"


def make_event(
    interview_id: str,
    payload: BaseModel,
    *,
    source: EventSource | str,
    at_seconds: float,
    event_id: str | None = None,
    wall_time: datetime | None = None,
) -> InterviewEvent:
    """Wrap a payload model (e.g. `TranscriptTurnEvent`) in the envelope.

    Validates here, so a malformed event fails in the code that built it, which
    is where the mistake is. `emit` itself never validates or raises.
    """
    return InterviewEvent(
        event_id=event_id or new_event_id(interview_id),
        interview_id=interview_id,
        at_seconds=max(0.0, round(at_seconds, 3)),
        wall_time=wall_time or datetime.now(UTC),
        source=EventSource(source),
        kind=EventKind(payload.kind),
        payload=payload,
    )


def _store_default() -> StoreFn:
    from live.store import insert_events

    return insert_events


class EventEmitter:
    """A bounded queue plus one background writer. One per process is enough."""

    def __init__(
        self,
        *,
        name: str = "",
        capacity: int = DEFAULT_CAPACITY,
        batch_size: int = DEFAULT_BATCH_SIZE,
        store: StoreFn | None = None,
    ):
        self.name = name
        self.capacity = capacity
        self.batch_size = batch_size
        self._store = store
        self._queue: asyncio.Queue[InterviewEvent] = asyncio.Queue(maxsize=capacity)
        self._pending: list[InterviewEvent] = []
        self._task: asyncio.Task | None = None
        self._closing = False
        self.emitted = 0
        self.dropped = 0
        self.rejected = 0
        self.written = 0
        self.duplicates = 0
        self.write_failures = 0
        self.lost_on_close = 0
        self.max_queue_depth = 0

    # -- the caller's side -----------------------------------------------------

    def emit(self, event: InterviewEvent) -> bool:
        """Queue one event. Never blocks, never raises. False if it was dropped."""
        if self._closing:
            self.dropped += 1
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1:
                # Once only: logging is I/O, and this path must stay O(1).
                logger.warning(f"[events {self.name}] queue full; dropping and counting")
            return False
        self.emitted += 1
        depth = self._queue.qsize()
        if depth > self.max_queue_depth:
            self.max_queue_depth = depth
        return True

    def stats(self) -> dict:
        """What happened to every event handed to `emit`. Goes into session metrics."""
        return {
            "emitted": self.emitted,
            "dropped": self.dropped,
            "rejected": self.rejected,
            "written": self.written,
            "duplicates": self.duplicates,
            "write_failures": self.write_failures,
            "lost_on_close": self.lost_on_close,
            "queue_capacity": self.capacity,
            "max_queue_depth": self.max_queue_depth,
        }

    # -- the writer ------------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"event-writer {self.name}")

    async def close(self, timeout: float = 2.0) -> dict:
        """Stop taking events, flush for up to `timeout`, then stop. Returns stats."""
        self._closing = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (self._queue.qsize() or self._pending) and loop.time() < deadline and self._task:
            await asyncio.sleep(0.02)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.lost_on_close = self._queue.qsize() + len(self._pending)
        if self.dropped or self.lost_on_close or self.rejected:
            logger.warning(f"[events {self.name}] closed: {self.stats()}")
        return self.stats()

    async def _run(self) -> None:
        store = self._store or _store_default()
        wait = RETRY_FIRST_SECONDS
        while True:
            if not self._pending:
                first = await self._queue.get()
                batch = [first]
                # Take whatever else is already waiting, without waiting for
                # more: no artificial delay at low rates, real batches at high.
                while len(batch) < self.batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                self._pending = self._validated(batch)
                if not self._pending:
                    continue
            try:
                stored, refused = await store(self._pending)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  the store is down: keep the batch, wait, retry
                self.write_failures += 1
                if self.write_failures == 1 or self.write_failures % 20 == 0:
                    logger.warning(f"[events {self.name}] store write failed "
                                   f"({self.write_failures}x), retrying: {exc}")
                await asyncio.sleep(wait)
                wait = min(wait * 2, RETRY_MAX_SECONDS)
                continue
            wait = RETRY_FIRST_SECONDS
            self.written += len(stored)
            self.rejected += len(refused)
            self.duplicates += len(self._pending) - len(stored) - len(refused)
            self._pending = []

    def _validated(self, batch: list) -> list[InterviewEvent]:
        """Re-validate before storing: a model built with `model_construct`, or
        something that is not an event at all, is refused and counted here."""
        good = []
        for item in batch:
            try:
                if not isinstance(item, InterviewEvent):
                    raise TypeError(f"not an InterviewEvent: {type(item).__name__}")
                good.append(InterviewEvent.model_validate(item.model_dump()))
            except (ValidationError, TypeError, ValueError, AttributeError) as exc:
                self.rejected += 1
                logger.warning(f"[events {self.name}] refused an invalid event: {exc}")
        return good


# -- the web process's own emitter ---------------------------------------------------
#
# Events that start in the web process (the proctoring intake's integrity
# signals) go through one emitter started by the app's lifespan.

_web: EventEmitter | None = None


def web_emitter() -> EventEmitter:
    global _web
    if _web is None:
        _web = EventEmitter(name="web")
    return _web


async def start_web_emitter() -> None:
    await web_emitter().start()


async def stop_web_emitter() -> dict | None:
    global _web
    if _web is None:
        return None
    stats = await _web.close()
    _web = None
    return stats
