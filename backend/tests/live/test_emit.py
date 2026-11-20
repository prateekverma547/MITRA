"""emit() never blocks; drops are counted; the writer stores in order, once.

Requirement (DEV_PLAN.md section 5): "emit() is non-blocking, with a bounded
queue that drops on overflow and counts the drops; a test asserts this."
"""

import asyncio
import inspect
import time

import pytest

from live.emit import EventEmitter
from live.store import all_events, events_after, insert_events
from tests.live.support import fresh_db, note_event, seed_interview, turn_event

IID = "int_emit00000001"
N = 20_000
#: One emit is a put_nowait on an in-memory queue: microseconds. 5 ms means it
#: waited on something, or the machine paused the process. A blocking emit is
#: slow on (nearly) every call once the queue is full; a busy machine makes a
#: rare call slow (seen once: 5.3 ms at load average 14.7, C-78). So the test
#: fails when more than MAX_SLOW_EMITS calls are slow, not on one outlier.
MAX_SINGLE_EMIT_SECONDS = 0.005
MAX_SLOW_EMITS = 20


def _events(n: int):
    return [turn_event(IID, i) for i in range(n)]


def _time_emits(emitter: EventEmitter, events) -> tuple[int, float]:
    """(how many emits took 5 ms or more, total seconds)."""
    slow = 0
    started = time.perf_counter()
    for e in events:
        t0 = time.perf_counter()
        emitter.emit(e)
        if time.perf_counter() - t0 >= MAX_SINGLE_EMIT_SECONDS:
            slow += 1
            if slow > MAX_SLOW_EMITS:
                break  # fail fast: a blocking emit, no need for 20,000
    return slow, time.perf_counter() - started


def test_emit_is_a_plain_function_not_a_coroutine():
    # A coroutine could be awaited and wait; a plain function cannot.
    assert not inspect.iscoroutinefunction(EventEmitter.emit)


async def test_emit_never_blocks_when_the_store_hangs():
    hung = asyncio.Event()  # never set: the store never answers

    async def hanging_store(batch):
        await hung.wait()

    emitter = EventEmitter(name="hang", capacity=100, batch_size=10, store=hanging_store)
    await emitter.start()
    await asyncio.sleep(0)
    events = _events(N)
    slow, total = _time_emits(emitter, events)
    assert slow <= MAX_SLOW_EMITS, slow
    assert total < 1.0, total
    # Every event is accounted for: queued (at most capacity, plus the batch the
    # writer is holding) or dropped and counted.
    assert emitter.emitted + emitter.dropped == N
    assert emitter.emitted <= 100 + 10
    assert emitter.dropped >= N - 110
    stats = await emitter.close(timeout=0.1)
    assert stats["dropped"] == emitter.dropped
    assert stats["lost_on_close"] == emitter.emitted  # nothing was written
    assert stats["written"] == 0


async def test_emit_never_blocks_when_the_store_is_down():
    calls = 0

    async def down(batch):
        nonlocal calls
        calls += 1
        raise ConnectionError("database unreachable")

    emitter = EventEmitter(name="down", capacity=100, batch_size=10, store=down)
    await emitter.start()
    await asyncio.sleep(0.01)
    slow, total = _time_emits(emitter, _events(N))
    assert slow <= MAX_SLOW_EMITS, slow
    assert total < 1.0, total
    await asyncio.sleep(0.05)
    assert calls >= 1 and emitter.write_failures >= 1
    stats = await emitter.close(timeout=0.1)
    assert stats["dropped"] > 0 and stats["dropped"] + stats["emitted"] == N


async def test_a_store_outage_loses_nothing_when_the_queue_has_room():
    stored: list = []
    failures = 3

    async def flaky(batch):
        nonlocal failures
        if failures:
            failures -= 1
            raise ConnectionError("blip")
        stored.extend(e.event_id for e in batch)
        return [e.event_id for e in batch], []

    import live.emit as emit_module

    emitter = EventEmitter(name="flaky", capacity=1000, store=flaky)
    original = emit_module.RETRY_FIRST_SECONDS
    emit_module.RETRY_FIRST_SECONDS = 0.01
    try:
        await emitter.start()
        for e in _events(500):
            assert emitter.emit(e)
        stats = await emitter.close(timeout=3.0)
    finally:
        emit_module.RETRY_FIRST_SECONDS = original
    assert stats["written"] == 500 and stats["dropped"] == 0 and stats["lost_on_close"] == 0
    assert stats["write_failures"] == 3
    assert stored == [f"{IID}:t{i:05d}" for i in range(500)]  # order kept through retries


async def test_an_invalid_event_is_refused_and_counted_not_stored():
    from shared.interview_contracts import InterviewEvent

    stored: list = []

    async def store(batch):
        stored.extend(batch)
        return [e.event_id for e in batch], []

    good = turn_event(IID, 1)
    # Built without validation, with a source the contract forbids for this kind.
    bad = InterviewEvent.model_construct(**{**dict(good), "event_id": "bad", "source": "browser"})
    emitter = EventEmitter(name="v", store=store)
    await emitter.start()
    emitter.emit(bad)
    emitter.emit("not an event")  # type: ignore[arg-type]
    emitter.emit(good)
    stats = await emitter.close()
    assert [e.event_id for e in stored] == [good.event_id]
    assert stats["rejected"] == 2


async def test_emit_after_close_is_dropped_and_counted():
    emitter = EventEmitter(name="closed", store=insert_events)
    await emitter.close()
    assert emitter.emit(turn_event(IID, 1)) is False
    assert emitter.stats()["dropped"] == 1


# -- the real store ----------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path, monkeypatch):
    database = await fresh_db(tmp_path, monkeypatch, "emit.db")
    await seed_interview(IID)
    yield database
    await database.reset_engine()


async def test_the_real_writer_stores_every_event_in_emission_order(db):
    emitter = EventEmitter(name="order")
    await emitter.start()
    sent = []
    for i in range(300):
        event = turn_event(IID, i) if i % 3 else note_event(IID, i)
        sent.append(event.event_id)
        assert emitter.emit(event)
        if i % 50 == 0:
            await asyncio.sleep(0)  # let the writer take partial batches too
    stats = await emitter.close(timeout=5)
    assert stats["written"] == 300 and stats["dropped"] == 0
    stored = await all_events(IID)
    assert [e.event_id for e in stored] == sent


async def test_writes_are_idempotent_by_event_id(db):
    events = _events(5)
    first, _ = await insert_events(events)
    again, refused = await insert_events(events)
    assert len(first) == 5 and again == [] and refused == []
    assert len(await events_after(IID, 0)) == 5

    emitter = EventEmitter(name="dupe")
    await emitter.start()
    for e in events:
        emitter.emit(e)
    stats = await emitter.close()
    assert stats["duplicates"] == 5 and stats["written"] == 0
