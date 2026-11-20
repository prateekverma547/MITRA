"""The relay: every stored event, in order, resumable from Last-Event-ID with no gap."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from live import relay
from live.emit import EventEmitter
from live.store import insert_events
from tests.live.support import ADMIN_PASSWORD, ended_event, fresh_db, seed_interview, turn_event

IID = "int_relay0000001"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    database = await fresh_db(tmp_path, monkeypatch, "relay.db")
    await seed_interview(IID)
    monkeypatch.setattr(relay, "END_GRACE_SECONDS", 0.05)
    yield database
    await database.reset_engine()


async def _never() -> bool:
    return False


def _parse(chunks: list[str]) -> list[tuple[int, dict]]:
    out = []
    for chunk in chunks:
        if chunk.startswith("id: "):
            id_line, data_line = chunk.strip().split("\n")
            out.append((int(id_line[4:]), json.loads(data_line[6:])))
    return out


async def _collect(after: int, *, until_end: bool = True, limit: int | None = None) -> tuple[list, list]:
    chunks = []
    async for chunk in relay.stream(IID, after, is_disconnected=_never, poll_seconds=0.01):
        chunks.append(chunk)
        if limit is not None and len(_parse(chunks)) >= limit:
            break
    return _parse(chunks), chunks


async def test_streams_everything_in_order_then_ends(db):
    await insert_events([turn_event(IID, i) for i in range(10)] + [ended_event(IID)])
    got, chunks = await _collect(0)
    assert chunks[0] == "retry: 1000\n\n"
    assert chunks[-1].startswith("event: end")
    assert [p["event_id"] for _, p in got] == [f"{IID}:t{i:05d}" for i in range(10)] + [f"{IID}:end"]
    seqs = [s for s, _ in got]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_resume_after_any_point_returns_exactly_the_rest(db):
    await insert_events([turn_event(IID, i) for i in range(20)] + [ended_event(IID)])
    everything, _ = await _collect(0)
    for cut in (0, 1, 7, 19, 20):
        resume_from = everything[cut - 1][0] if cut else 0
        rest, _ = await _collect(resume_from)
        assert rest == everything[cut:], f"resume after event {cut} skipped or repeated"


async def test_follows_events_written_while_streaming(db):
    emitter = EventEmitter(name="live")
    await emitter.start()

    async def produce():
        for i in range(30):
            emitter.emit(turn_event(IID, i))
            await asyncio.sleep(0.005)
        emitter.emit(ended_event(IID))
        await emitter.close()

    producer = asyncio.create_task(produce())
    got, _ = await _collect(0)
    await producer
    assert [p["event_id"] for _, p in got] == [f"{IID}:t{i:05d}" for i in range(30)] + [f"{IID}:end"]


async def test_two_writers_interleaved_leave_no_gap_for_a_resuming_reader(db):
    """Bot and web process both write for one interview; a reader that resumes
    mid-way must still see every event (seq order equals commit order)."""
    a, b = EventEmitter(name="bot"), EventEmitter(name="web")
    await a.start()
    await b.start()
    for i in range(0, 200, 2):
        a.emit(turn_event(IID, i))
        b.emit(turn_event(IID, i + 1))
        if i % 20 == 0:
            await asyncio.sleep(0)
    await a.close(timeout=5)
    await b.close(timeout=5)
    await insert_events([ended_event(IID, 300.0)])
    everything, _ = await _collect(0)
    assert len(everything) == 201
    first_half, _ = await _collect(0, until_end=False, limit=77)
    rest, _ = await _collect(first_half[-1][0])
    assert first_half + rest == everything


def test_parse_last_event_id():
    assert relay.parse_last_event_id(None, None) == 0
    assert relay.parse_last_event_id("12", "3") == 12  # the header wins
    assert relay.parse_last_event_id("", "3") == 3
    for bad in ("x", "-1", "1.5"):
        with pytest.raises(ValueError):
            relay.parse_last_event_id(bad, None)


# -- the route --------------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/route.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setattr(relay, "END_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(relay, "POLL_SECONDS", 0.01)
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(seed_interview, IID)
        c.portal.call(insert_events, [turn_event(IID, i) for i in range(6)] + [ended_event(IID)])
        yield c
    await db.reset_engine()


def _sse(client, **kw) -> list[tuple[int, dict]]:
    chunks = []
    with client.stream("GET", f"/api/live/interviews/{IID}/events", **kw) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        buffer = ""
        for text in r.iter_text():
            buffer += text
        chunks = buffer.split("\n\n")
    return _parse([c + "\n\n" for c in chunks if c])


def test_route_needs_the_admin_session(client):
    assert client.get(f"/api/live/interviews/{IID}/events").status_code == 401


def test_route_streams_and_resumes_from_the_last_event_id_header(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    everything = _sse(client)
    assert len(everything) == 7
    resumed = _sse(client, headers={"Last-Event-ID": str(everything[2][0])})
    assert resumed == everything[3:]
    by_query = _sse(client, params={"after": str(everything[4][0])})
    assert by_query == everything[5:]


def test_route_refuses_a_bad_resume_point_and_an_unknown_interview(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    r = client.get(f"/api/live/interviews/{IID}/events", headers={"Last-Event-ID": "abc"})
    assert r.status_code == 400
    assert client.get("/api/live/interviews/int_nope/events").status_code == 404


# -- keep-alives the browser can see (B-51) ------------------------------------------------


async def test_a_quiet_stream_sends_a_ping_event_not_a_comment(db, monkeypatch):
    """EventSource never surfaces SSE comments, so the keep-alive must be a real
    event. It carries no id, so it never moves the viewer's resume point."""
    assert relay.HEARTBEAT_SECONDS <= 15.0  # B's dead-stream rule assumes at most 15 s of silence
    assert relay.DEAD_AFTER_SECONDS >= 2 * relay.HEARTBEAT_SECONDS
    monkeypatch.setattr(relay, "HEARTBEAT_SECONDS", 0.05)

    async def first_ping():
        seen = []
        async for chunk in relay.stream(IID, 0, is_disconnected=_never, poll_seconds=0.01):
            seen.append(chunk)
            if not chunk.startswith("retry:"):
                return seen

    chunks = await asyncio.wait_for(first_ping(), timeout=2.0)
    ping = chunks[-1]
    assert ping == "event: ping\ndata: {}\n\n"
    assert not ping.startswith(":") and "id:" not in ping


async def test_events_keep_flowing_without_pings_between_them(db, monkeypatch):
    """A ping is sent only after HEARTBEAT_SECONDS of silence: a busy stream is not padded."""
    await insert_events([turn_event(IID, i) for i in range(5)] + [ended_event(IID)])
    _, chunks = await _collect(0)
    assert relay.PING not in chunks
