"""Live status readings: same pass as the intake, rate-limited, exact payload,
on the stream at once, latest-on-reconnect, never stored."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from live import relay
from live.status import board
from proctoring import intake, status
from tests.live.support import ADMIN_PASSWORD, seed_interview

IID = "int_stat00000001"
OTHER = "int_stat00000002"
READING = {"kind": "integrity_status", "distance_cm": 62, "distance_in_band": True,
           "faces_in_view": 1, "page_visible": True}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/status.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setattr(relay, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(relay, "END_GRACE_SECONDS", 0.05)
    intake.limiter.reset()
    status.status_limiter.reset()
    board.clear()
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(seed_interview, IID)
        c.portal.call(lambda: seed_interview(OTHER, candidate_id="cand_other"))
        yield c
    await db.reset_engine()
    status.status_limiter.reset()
    board.clear()


def send(client, reading=READING, *, token=None, interview=IID):
    token = intake.issue_token(interview) if token is None else token
    return client.post(f"/api/proctoring/interviews/{interview}/status", json=reading,
                       headers={"Authorization": f"Bearer {token}"} if token else {})


def test_a_valid_reading_is_accepted(client):
    r = send(client)
    assert r.status_code == 200 and r.json()["accepted"] is True
    assert board.latest(IID).event["payload"] == READING


@pytest.mark.parametrize("token", ["", "v1.proctoring-intake.int_stat00000001.9999999999.00"])
def test_needs_this_interviews_intake_token(client, token):
    assert send(client, token=token).status_code == 401


def test_another_interviews_token_is_refused(client):
    assert send(client, token=intake.issue_token(OTHER)).status_code == 401


@pytest.mark.parametrize("bad", [
    {**READING, "frame": "data:image/jpeg;base64,AAAA"},  # extra field
    {**READING, "kind": "integrity_signal"},               # not a status reading
    {**READING, "distance_cm": -4},                         # contract bound
    {**READING, "faces_in_view": "one"},
])
def test_the_body_is_exactly_the_status_payload(client, bad):
    assert send(client, bad).status_code == 422


def test_rate_limit_about_one_a_second_with_a_small_burst(client):
    allowed = [send(client).status_code for _ in range(status.STATUS_BURST + 2)]
    assert allowed[: status.STATUS_BURST] == [200] * status.STATUS_BURST
    assert allowed[status.STATUS_BURST:] == [429, 429]
    assert send(client, interview=OTHER).status_code == 200  # its own bucket


def test_the_bucket_refills_at_about_one_a_second():
    bucket = status.TokenBucket(rate=1.0, burst=3)
    assert [bucket.allow("i", now=0.0) for _ in range(4)] == [True, True, True, False]
    assert bucket.allow("i", now=0.5) is False
    assert bucket.allow("i", now=1.0) is True


def test_an_interview_that_is_not_running_takes_no_readings(client):
    client.portal.call(lambda: seed_interview("int_stat00000003", candidate_id="c3",
                                              status="completed", ended_ago=3600))
    assert send(client, interview="int_stat00000003").status_code == 409


def test_readings_are_never_stored(client):
    from live.emit import web_emitter

    for distance in (60, 61, 62):
        status.status_limiter.reset()
        send(client, {**READING, "distance_cm": distance})

    async def counts():
        from sqlalchemy import func, select

        from app.db import get_sessionmaker
        from live.models import InterviewEventRecord
        from proctoring.models import IntegrityEventRecord

        emitter = web_emitter()
        for _ in range(50):  # let the web writer drain anything it was given
            if emitter._queue.empty() and not emitter._pending:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        async with get_sessionmaker()() as s:
            return (await s.scalar(select(func.count()).select_from(InterviewEventRecord)),
                    await s.scalar(select(func.count()).select_from(IntegrityEventRecord)))

    assert client.portal.call(counts) == (0, 0)


# -- on the stream ----------------------------------------------------------------------


async def _never():
    return False


async def _read(interview_id, after=0, *, want, timeout=3.0):
    out = []

    async def go():
        async for chunk in relay.stream(interview_id, after, is_disconnected=_never, poll_seconds=0.5):
            if chunk.startswith("data:"):
                out.append(json.loads(chunk[6:]))
            if len(out) >= want:
                return

    await asyncio.wait_for(go(), timeout)
    return out


def test_a_reading_reaches_the_stream_before_the_next_poll(client):
    async def scenario():
        reader = asyncio.create_task(_read(IID, want=1))
        await asyncio.sleep(0.05)  # the viewer is connected and waiting
        loop = asyncio.get_running_loop()
        started = loop.time()
        status.publish(IID, status.StatusBody.model_validate(READING),
                       started_at=__import__("datetime").datetime.now(__import__("datetime").UTC))
        got = await reader
        return got, loop.time() - started

    got, waited = client.portal.call(scenario)
    assert got[0]["kind"] == "integrity_status" and got[0]["payload"] == READING
    assert waited < 0.25  # woken by the publish, not by the 0.5 s poll


def test_a_reconnecting_viewer_gets_the_latest_reading_at_once(client):
    for distance in (58, 64):
        status.status_limiter.reset()
        send(client, {**READING, "distance_cm": distance})

    async def connect():
        loop = asyncio.get_running_loop()
        started = loop.time()
        got = await _read(IID, want=1)
        return got, loop.time() - started

    got, waited = client.portal.call(connect)
    assert got[0]["payload"]["distance_cm"] == 64  # the latest, not the first
    assert waited < 0.25


def test_status_messages_carry_no_id_so_resume_is_unaffected():
    chunk = relay.sse_status({"kind": "integrity_status"})
    assert "id:" not in chunk and chunk.startswith("data: ")
