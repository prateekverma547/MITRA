"""Reading and writing the stored event stream.

Two properties the relay depends on:

- **Idempotent writes.** `event_id` is unique and inserts skip conflicts, so a
  batch retried after a lost acknowledgement stores nothing twice.
- **No row becomes visible behind the cursor.** The relay resumes from "after
  seq N". That is only gap-free if a row with a lower seq can never commit after
  a row with a higher one. SQLite serialises every write transaction, so it
  holds there by construction. Postgres hands out sequence numbers before
  commit, so two writers (a bot process and the web process's intake) could
  commit out of order; each write transaction therefore takes one advisory
  lock first, which makes seq order and commit order the same. The lock is held
  for one small INSERT, a few milliseconds.
"""

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from live.models import InterviewEventRecord
from shared.interview_contracts import InterviewEvent

#: Any constant works; it only has to be the same for every writer.
_WRITE_LOCK_KEY = 0x4D49_5452  # "MITR"


def _row(event: InterviewEvent, org_id: str | None = None) -> dict:
    return {
        "event_id": event.event_id,
        "org_id": org_id,
        "interview_id": event.interview_id,
        "at_seconds": event.at_seconds,
        "wall_time": event.wall_time,
        "source": event.source.value,
        "kind": event.kind.value,
        "payload": event.model_dump(mode="json"),
    }


def _insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(InterviewEventRecord)


async def _serialise_writers(session: AsyncSession) -> None:
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _WRITE_LOCK_KEY})


async def insert_events(events: Sequence[InterviewEvent]) -> tuple[list[str], list[str]]:
    """Store a batch. Returns (event ids newly stored, event ids refused).

    A duplicate event_id is skipped silently: that is the idempotency. A row the
    database refuses for another reason (an interview id that does not exist,
    on a database that enforces foreign keys) is refused on its own, so one bad
    event never holds up the rest of the batch.
    """
    if not events:
        return [], []
    rows = [_row(e) for e in events]
    maker = get_sessionmaker()
    try:
        async with maker() as session:
            await _serialise_writers(session)
            stmt = (
                _insert(session.bind.dialect.name)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["event_id"])
                .returning(InterviewEventRecord.event_id)
            )
            stored = list((await session.execute(stmt)).scalars())
            await session.commit()
            return stored, []
    except IntegrityError:
        pass

    stored, refused = [], []
    for row in rows:
        try:
            async with maker() as session:
                await _serialise_writers(session)
                stmt = (
                    _insert(session.bind.dialect.name)
                    .values(row)
                    .on_conflict_do_nothing(index_elements=["event_id"])
                    .returning(InterviewEventRecord.event_id)
                )
                stored.extend((await session.execute(stmt)).scalars())
                await session.commit()
        except IntegrityError:
            refused.append(row["event_id"])
    return stored, refused


async def events_after(interview_id: str, after_seq: int, *, limit: int = 500) -> list[tuple[int, dict]]:
    """(seq, InterviewEvent JSON) for one interview, after `after_seq`, in seq order."""
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            select(InterviewEventRecord.seq, InterviewEventRecord.payload)
            .where(
                InterviewEventRecord.interview_id == interview_id,
                InterviewEventRecord.seq > after_seq,
            )
            .order_by(InterviewEventRecord.seq)
            .limit(limit)
        )
        return [(seq, payload) for seq, payload in rows]


async def all_events(interview_id: str) -> list[InterviewEvent]:
    """Every stored event for one interview, validated, in seq order."""
    out: list[InterviewEvent] = []
    after = 0
    while True:
        batch = await events_after(interview_id, after)
        if not batch:
            return out
        out.extend(InterviewEvent.model_validate(p) for _, p in batch)
        after = batch[-1][0]
