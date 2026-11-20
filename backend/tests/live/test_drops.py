"""The status-reading drop detector (wave 1; D-30). Simulated readings on a fake
clock: it fires on a real gap of 3 missed readings, never before, never for an
interview that is not running, and writes the resume for the bot to read."""

from datetime import UTC, datetime, timedelta

import pytest

from live import drops
from live.status import StatusBoard
from live.store import all_events
from tests.live.support import fresh_db, seed_interview

IID = "int_drops0000001"
READING = {"kind": "integrity_status"}


@pytest.fixture
async def db(tmp_path, monkeypatch):
    database = await fresh_db(tmp_path, monkeypatch, "drops.db")
    await seed_interview(IID, status="in_progress", started_ago=200,
                         transcript_zero_at=datetime.now(UTC) - timedelta(seconds=195))
    yield database
    await database.reset_engine()


async def run(readings: list[float], until: float, *, tick_every: float = 0.25, iid: str = IID):
    """Publish readings at the given times and tick the detector; returns (time, what) it wrote."""
    board = StatusBoard()
    detector = drops.DropDetector(status_board=board)
    pending, written, t = sorted(readings), [], 0.0
    while t <= until:
        while pending and pending[0] <= t:
            board.publish(iid, READING, now=pending.pop(0))
        for _, what in await detector.tick(now=t):
            written.append((round(t, 2), what))
        t = round(t + tick_every, 2)
    return written


async def test_it_fires_three_seconds_after_the_last_reading_and_not_before(db):
    readings = [float(s) for s in range(0, 11)]           # a reading a second until 10 s, then nothing
    written = await run(readings, until=20.0)
    assert written == [(13.0, "dropped")]                  # 3 missed readings: 13.0 s, at the tick
    assert drops.DROP_AFTER_SECONDS == 3.0


async def test_healthy_jitter_never_fires(db):
    # D-30's largest healthy gap was 1.29 s; here gaps of up to 2.75 s.
    readings, t = [], 0.0
    for gap in [1.0, 1.29, 0.8, 2.75, 1.1, 2.5, 1.0] * 6:
        t += gap
        readings.append(round(t, 2))
    assert await run(readings, until=readings[-1] + 2.5) == []


async def test_readings_resuming_write_the_resume_with_its_gap_and_the_bot_can_read_both(db):
    readings = [float(s) for s in range(0, 6)] + [17.0, 18.0, 19.0]
    assert await run(readings, until=20.0) == [(8.0, "dropped"), (17.0, "resumed")]
    signals = await drops.signals_after(IID)
    assert [(s.kind, s.detected_by, s.gap_seconds) for s in signals] == [
        ("dropped", "status_readings", None), ("resumed", "status_readings", 12.0)]
    assert await drops.signals_after(IID, signals[0].seq) == signals[1:]  # the bot keeps its last seq
    [dropped] = [e for e in await all_events(IID) if e.kind.value == "candidate_dropped"]
    assert dropped.source.value == "server" and dropped.payload.detected_by.value == "status_readings"
    # The drop is placed at the last reading before the gap (about 5 s ago on a
    # 195 s-old interview clock), not at the moment it was noticed.
    assert 180 <= dropped.at_seconds <= 200
    # The server never decides counting: no candidate_returned from it.
    assert not [e for e in await all_events(IID) if e.kind.value == "candidate_returned"]


@pytest.mark.parametrize("status", ["completed", "scheduled", "failed"])
async def test_an_interview_that_is_not_running_never_has_a_drop(db, status):
    iid = f"int_drops_{status}"
    await seed_interview(iid, candidate_id=f"c_{status}", status=status,
                         started_ago=None if status == "scheduled" else 300, ended_ago=10 if status != "scheduled" else None)
    assert await run([0.0, 1.0, 2.0], until=10.0, iid=iid) == []
    assert await drops.signals_after(iid) == []


async def test_an_ended_interview_is_asked_about_once_not_on_every_tick(db, monkeypatch):
    iid = "int_drops_ended1"
    await seed_interview(iid, candidate_id="c_ended1", status="completed", started_ago=300, ended_ago=10)
    asked = []
    real = drops.DropDetector._interview_seconds

    async def counting(self, *a):
        asked.append(a)
        return await real(self, *a)

    monkeypatch.setattr(drops.DropDetector, "_interview_seconds", counting)
    assert await run([0.0, 1.0], until=30.0, iid=iid) == []
    assert len(asked) == 1
