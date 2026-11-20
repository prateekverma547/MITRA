"""The intake: integrity events posted by the candidate's own browser.

**Auth.** An interview-scoped token, issued in the join response
(app/interviews.py). It is safe to hand to the browser because it can do
exactly one thing: post browser integrity events (and live status readings,
proctoring/status.py) for that one interview, for `TOKEN_TTL_SECONDS`. It is
HMAC-signed with a purpose ("proctoring-intake"), so it is not a Daily token,
not an admin session and not an evidence link, and it cannot be pointed at
another interview. Holding it reads nothing.

**What is refused**, per event, with its position, its event id (only when it is a plain
identifier) and a code; nothing else from the request is returned (D-92):
- anything that fails the `IntegrityEvent` contract;
- any source but `browser`, and any after-call kind: a browser cannot report
  what only the recording's analysis may find;
- events outside the interview's time window: later than it has been running
  (plus `CLOCK_SLACK_SECONDS`).

**An event arrives twice: open, then closed** (the candidate app's sink,
frontend/candidate-app/src/proctoring/sink.ts and engine.ts). The first copy is
stored as it is. A second copy with the same event_id is applied only when it
closes a still-open event and changes nothing but what closing changes
(`classify_repeat`, the rule in one place). Everything else with a known
event_id is ignored or refused with a reason, and a stored event is never
rewritten after it has closed.

**Limits:** `MAX_BODY_BYTES` per request, `MAX_EVENTS_PER_BATCH` per batch,
`RATE_LIMIT_BATCHES` per `RATE_WINDOW_SECONDS` per interview.

Images, frames and audio never arrive here: the batch schema forbids any field
it does not name.
"""

import re
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update

from app.db import Interview, InterviewStatus, get_sessionmaker
from live import tokens
from live.clock import interview_time_now
from live.emit import make_event, web_emitter
from proctoring.models import IntegrityAccommodationRecord, IntegrityEventRecord
from shared.interview_contracts import (
    AFTER_CALL_ONLY,
    EventSource,
    IntegrityAccommodation,
    IntegrityEvent,
    IntegritySignal,
    IntegritySource,
)

PURPOSE = "proctoring-intake"
#: The candidate's Daily token lives 90 minutes (CANDIDATE_TOKEN_MINUTES); this
#: one lives as long and no longer. A rejoin gets a fresh one.
TOKEN_TTL_SECONDS = 90 * 60
MAX_BODY_BYTES = 64 * 1024
MAX_EVENTS_PER_BATCH = 100
RATE_LIMIT_BATCHES = 30
RATE_WINDOW_SECONDS = 60.0
#: Kept as a margin, not as an alignment: the browser's clock starts when the
#: call screen mounts, after the server's `started_at`, so a browser time is
#: normally below the server's elapsed time (C-27, C-36).
CLOCK_SLACK_SECONDS = 30.0
#: The browser may flush its last batch just after the call ends.
LATE_GRACE_SECONDS = 120.0

#: What the closing copy may change, and why each one:
#: - `end_seconds`: the close itself (engine.ts closeKind sets it, never below
#:   the start).
#: - `repeats`: a repeated-gaze event is opened with repeats 1 and closed with
#:   the count (engine.ts gazeOut, the "repeated" branch). It may only grow.
#: - `confidence`: a room's detector may only know how sure it is once the event ends.
CLOSE_MAY_CHANGE = frozenset({"end_seconds", "repeats", "confidence"})
#: Set by the server when it stores an event; never compared, never taken from a client.
SERVER_FIELDS = frozenset({"received_at", "received_interview_seconds"})
#: An event id is a plain identifier: it is the uniqueness key, and the only request value
#: ever returned (in a refusal), so nothing else can be reflected back (D-92).
EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Outcomes of a second copy, for `classify_repeat`.
CLOSE = "close"
DUPLICATE = "duplicate"
REFUSED = "refused"


def issue_token(interview_id: str) -> str:
    token, _ = tokens.issue(PURPOSE, interview_id, TOKEN_TTL_SECONDS)
    return token


def token_valid_for(token: str | None, interview_id: str) -> bool:
    return tokens.verify(token, PURPOSE, interview_id) is not None


class IntakeBatch(BaseModel):
    """The request body. `extra="forbid"`: nothing unnamed (a frame, an image) gets in."""

    model_config = ConfigDict(extra="forbid")

    events: list[dict] = Field(default_factory=list, max_length=MAX_EVENTS_PER_BATCH)
    accommodation: IntegrityAccommodation | None = None


class RateLimiter:
    """Sliding window per key, in memory. One web replica (CLAUDE.md), so enough."""

    def __init__(self, limit: int = RATE_LIMIT_BATCHES, window: float = RATE_WINDOW_SECONDS):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


class IntakeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def window_end(interview: Interview, now: datetime) -> float:
    """How many interview-clock seconds an event may claim, or raise if closed."""
    started = _aware(interview.started_at)
    if started is None:
        raise IntakeError(409, "This interview has not started.")
    if interview.status == InterviewStatus.IN_PROGRESS:
        return (now - started).total_seconds() + CLOCK_SLACK_SECONDS
    ended = _aware(interview.ended_at)
    if ended is not None and (now - ended).total_seconds() <= LATE_GRACE_SECONDS:
        return (ended - started).total_seconds() + CLOCK_SLACK_SECONDS
    raise IntakeError(409, "This interview is not running.")


class Refusal(ValueError):
    """An event refused, with a code (never words from the request)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _code(exc: ValidationError) -> str:
    """A refusal code from a contract failure: which field, never the value."""
    err = exc.errors()[0]
    if "only found after the call" in err.get("msg", ""):  # the contract's own rule (AFTER_CALL_ONLY)
        return "after_call_only"
    field = err["loc"][0] if err.get("loc") else None
    if field == "kind":
        return "unknown_type"
    if field == "source":
        return "unknown_source"
    if field == "detail" and "decision 12" in err.get("msg", ""):
        return "detail_not_neutral"
    if err.get("type") == "missing":
        return "missing_field"
    return f"invalid_{field}" if isinstance(field, str) and field.isidentifier() else "invalid_event"


def safe_event_id(raw: dict) -> str | None:
    value = raw.get("event_id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and EVENT_ID.match(value) else None


def _check(raw: dict, limit: float) -> IntegrityEvent:
    if not isinstance(raw, dict):
        raise Refusal("invalid_event")
    if safe_event_id(raw) is None:
        raise Refusal("invalid_event_id")
    try:
        event = IntegrityEvent.model_validate({k: v for k, v in raw.items() if k not in SERVER_FIELDS})
    except ValidationError as exc:
        raise Refusal(_code(exc)) from exc
    if event.source is not IntegritySource.BROWSER:
        raise Refusal("only_browser_source")
    if event.kind in AFTER_CALL_ONLY:
        raise Refusal("after_call_only")
    latest = event.end_seconds if event.end_seconds is not None else event.start_seconds
    if latest > limit:
        raise Refusal("outside_window")
    return event


def classify_repeat(stored: IntegrityEvent, new: IntegrityEvent) -> tuple[str, str | None]:
    """What to do with a copy whose event_id is already stored.

    Returns (CLOSE, None) when `new` closes `stored`; (DUPLICATE, reason) when it
    is ignored; (REFUSED, reason) when it contradicts what was stored. Pure, so
    the rule is tested on its own.
    """
    old, fresh = stored.model_dump(exclude=SERVER_FIELDS), new.model_dump(exclude=SERVER_FIELDS)
    if fresh == old:
        return DUPLICATE, None
    if stored.end_seconds is not None:
        if new.end_seconds is None:
            return REFUSED, "reopens_closed_event"
        # A second close, or anything else about a closed event: never rewritten.
        return DUPLICATE, "already_closed"
    if new.end_seconds is None:
        return REFUSED, "changes_open_event"
    changed = sorted(k for k in fresh if fresh[k] != old[k] and k not in CLOSE_MAY_CHANGE)
    if changed:
        return REFUSED, "closing_copy_changes_fields"
    if new.end_seconds < stored.start_seconds:
        return REFUSED, "ends_before_start"
    if new.repeats < stored.repeats:
        return REFUSED, "closing_copy_lowers_repeats"
    return CLOSE, None


def _insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(IntegrityEventRecord)


async def close_stored(session, interview_id: str, closing: IntegrityEvent) -> bool:
    """Apply a closing copy, only to a row that is still open.

    The `end_seconds IS NULL` condition is the database's half of the rule:
    two closes racing each other cannot both apply, whatever `classify_repeat`
    saw. True if this call closed it.
    """
    result = await session.execute(
        update(IntegrityEventRecord)
        .where(
            IntegrityEventRecord.interview_id == interview_id,
            IntegrityEventRecord.event_id == closing.event_id,
            IntegrityEventRecord.end_seconds.is_(None),
        )
        .values(end_seconds=closing.end_seconds, event=closing.model_dump(mode="json"))
    )
    return result.rowcount == 1


def _signal(interview_id: str, event: IntegrityEvent, *, closing: bool):
    """The stream's integrity_signal, at the event's start (interview seconds).

    The closing one is a second signal for the same integrity_event_id, with
    `end_seconds` taken from the closing copy (the close rule). Stream events
    stored before end_seconds existed carried the end as at_seconds instead
    (C-38); they still load.
    """
    suffix = ":closed" if closing else ""
    return make_event(
        interview_id,
        IntegritySignal(integrity_event_id=event.event_id, integrity_kind=event.kind,
                        reminder=event.reminder, reminder_index=event.reminder_index,
                        end_seconds=event.end_seconds if closing else None),
        source=EventSource.BROWSER,
        at_seconds=event.start_seconds,
        # Deterministic, so one integrity event never becomes two stream events
        # of the same meaning, however often its copies are resent.
        event_id=f"{interview_id}:integrity:{event.event_id}{suffix}",
    )


async def accept(interview_id: str, batch: IntakeBatch, *, now: datetime | None = None) -> dict:
    """Validate, store what is new, close what is closing, emit signals. Returns counts."""
    now = now or datetime.now(UTC)
    signals = []
    stored = closed = duplicates = 0
    ignored: list[dict] = []
    refused: list[dict] = []
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise IntakeError(404, "No such interview.")
        limit = window_end(interview, now)
        clock = interview_time_now(interview, now)

        for index, raw in enumerate(batch.events):
            try:
                event = _check(raw, limit)
            except Refusal as exc:
                # Only the position, a plain event id and a code: nothing else from the request.
                refused.append({"index": index, "event_id": safe_event_id(raw), "code": exc.code})
                continue
            event = event.model_copy(update={"received_at": now,
                                             "received_interview_seconds": clock["interview_seconds"]})

            inserted = (await session.execute(
                _insert(session.bind.dialect.name)
                .values(org_id=None, interview_id=interview_id, event_id=event.event_id,
                        kind=event.kind.value, source=event.source.value,
                        start_seconds=event.start_seconds, end_seconds=event.end_seconds,
                        event=event.model_dump(mode="json"))
                .on_conflict_do_nothing(index_elements=["interview_id", "event_id"])
                .returning(IntegrityEventRecord.event_id)
            )).scalar_one_or_none()
            if inserted is not None:
                stored += 1
                signals.append(_signal(interview_id, event, closing=False))
                if event.end_seconds is not None:  # arrived already closed (a paste)
                    signals.append(_signal(interview_id, event, closing=True))
                continue

            row = await session.scalar(
                select(IntegrityEventRecord.event).where(
                    IntegrityEventRecord.interview_id == interview_id,
                    IntegrityEventRecord.event_id == event.event_id,
                )
            )
            first = IntegrityEvent.model_validate(row)
            outcome, reason = classify_repeat(first, event)
            # the first arrival's receive time stays the event's receive time
            event = event.model_copy(update={k: getattr(first, k) for k in SERVER_FIELDS})
            if outcome == CLOSE and await close_stored(session, interview_id, event):
                closed += 1
                signals.append(_signal(interview_id, event, closing=True))
            elif outcome == REFUSED:
                refused.append({"index": index, "event_id": event.event_id, "code": reason})
            elif reason is None and outcome == DUPLICATE:
                duplicates += 1
            else:
                ignored.append({"index": index, "event_id": event.event_id, "code": reason or "already_closed"})

        accommodation_set = False
        if batch.accommodation is not None:
            existing = await session.get(IntegrityAccommodationRecord, interview_id)
            if existing is None:
                session.add(IntegrityAccommodationRecord(
                    interview_id=interview_id,
                    gaze_reminders_off=batch.accommodation.gaze_reminders_off,
                    note=batch.accommodation.note,
                ))
                accommodation_set = True
        await session.commit()

    emitter = web_emitter()
    for signal in signals:
        emitter.emit(signal)
    return {
        "stored": stored,
        "closed": closed,
        "duplicates": duplicates,
        "ignored": ignored,
        "refused": refused,
        "accommodation_set": accommodation_set,
        # The server is the clock: the browser re-syncs from every response.
        "clock": clock,
    }


async def stored_events(interview_id: str) -> list[IntegrityEvent]:
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(IntegrityEventRecord)
            .where(IntegrityEventRecord.interview_id == interview_id)
            .order_by(IntegrityEventRecord.start_seconds, IntegrityEventRecord.id)
        )
        return [IntegrityEvent.model_validate(r.event) for r in rows]
