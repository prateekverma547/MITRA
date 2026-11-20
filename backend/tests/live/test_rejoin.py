"""The rejoin pass and its exchange (phase 3, wave 1).

Requirements: a short-lived pass bound to the interview and its candidate,
expiring at the hard stop plus a margin; exchanged once for a fresh candidate
token for the SAME room; refused with a reason code after the interview ended,
after the rejoin limit (the bot's own event), when no bot is alive, or for a
wrong, used or other interview's pass; rate-limited; never containing or
logging the meeting password or the pass.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from live import rejoin
from live.credential_limits import limits
from live.emit import make_event
from live.store import insert_events
from shared.interview_contracts import CandidateReturned, EventSource, RejoinLimitReached
from tests.live.support import seed_interview

IID = "int_rejoin000001"
OTHER = "int_rejoin000002"
PASSWORD = "kmnp3478"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/rejoin.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import capacity, db, main
    from bot.services import daily

    minted: list = []

    async def fake_token(**kw):
        minted.append(kw)
        return f"fresh-daily-token-{len(minted)}"

    monkeypatch.setattr(daily, "create_meeting_token", fake_token)
    monkeypatch.setattr(capacity.registry, "_bots", {IID: SimpleNamespace(returncode=None)})
    limits.reset()
    await db.reset_engine()
    with TestClient(main.app) as c:
        c.minted = minted
        c.portal.call(lambda: seed_interview(IID, status="in_progress", started_ago=120, password=PASSWORD,
                                             planned_duration_minutes=40, planned_grace_minutes=5))
        c.portal.call(lambda: seed_interview(OTHER, candidate_id="cand_other", status="in_progress", started_ago=60))
        yield c
    limits.reset()
    await db.reset_engine()


def issue(client, iid=IID):
    from app import db

    async def go():
        async with db.get_sessionmaker()() as s:
            return await rejoin.issue_pass(await s.get(db.Interview, iid))

    return client.portal.call(go)


def exchange(client, value, iid=IID):
    return client.post("/api/live/rejoin", json={"interview_id": iid, "rejoin_pass": value})


def test_a_pass_gets_a_fresh_candidate_token_for_the_same_room_once(client):
    first = issue(client)
    r = exchange(client, first.value)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room_url"] == f"https://example.daily.co/room-{IID}" and body["token"] == "fresh-daily-token-1"
    [minted] = client.minted
    assert minted["room_name"] == f"room-{IID}" and minted["is_owner"] is False
    assert minted["user_id"] == f"candidate:{IID}"  # the candidate's own mark: still the candidate
    assert body["rejoin_pass"] != first.value and body["rejoins_counted"] == 0 and body["rejoins_left"] == 3
    again = exchange(client, first.value)  # single use
    assert again.status_code == 401 and again.json()["reason"] == "invalid_pass"
    assert exchange(client, body["rejoin_pass"]).status_code == 200  # the next pass works


def test_the_pass_expires_at_the_hard_stop_plus_the_margin(client):
    issued = issue(client)
    expected = datetime.now(UTC) - timedelta(seconds=120) + timedelta(minutes=40 + 5 + rejoin.REJOIN_PASS_MARGIN_MINUTES)
    assert abs((issued.expires_at - expected).total_seconds()) < 5


@pytest.mark.parametrize("value", ["", "garbage", "v1.rejoin.int_rejoin000001:abc.9999999999.deadbeef",
                                   "v1.proctoring-intake.int_rejoin000001.9999999999.00"])
def test_a_wrong_pass_is_refused(client, value):
    r = exchange(client, value)
    assert r.status_code in (401, 422) and (r.status_code == 422 or r.json()["reason"] == "invalid_pass")


def test_another_interviews_pass_is_refused(client):
    other = issue(client, OTHER)
    r = exchange(client, other.value, iid=IID)
    assert r.status_code == 401 and r.json()["reason"] == "invalid_pass"


@pytest.mark.parametrize("status", ["completed", "failed", "expired"])
def test_after_the_interview_ended_the_pass_is_refused(client, status):
    issued = issue(client)
    from app import db

    async def end():
        async with db.get_sessionmaker()() as s:
            (await s.get(db.Interview, IID)).status = status
            await s.commit()

    client.portal.call(end)
    r = exchange(client, issued.value)
    assert r.status_code == 410 and r.json()["reason"] == "interview_ended"
    assert client.minted == []


def test_after_the_rejoin_limit_the_pass_is_refused(client):
    issued = issue(client)
    client.portal.call(insert_events, [
        make_event(IID, CandidateReturned(gap_seconds=12, same_participant=True, counted=True, rejoin_number=1),
                   source=EventSource.BOT, at_seconds=30),
        make_event(IID, RejoinLimitReached(rejoins=4), source=EventSource.BOT, at_seconds=90)])
    r = exchange(client, issued.value)
    assert r.status_code == 409 and r.json()["reason"] == "rejoin_limit_reached"


def test_with_no_bot_alive_the_pass_is_refused(client, monkeypatch):
    from app import capacity

    issued = issue(client)
    monkeypatch.setattr(capacity.registry, "_bots", {IID: SimpleNamespace(returncode=0)})
    r = exchange(client, issued.value)
    assert r.status_code == 409 and r.json()["reason"] == "interviewer_gone"


def test_counts_come_from_the_bots_own_events(client):
    client.portal.call(insert_events, [make_event(
        IID, CandidateReturned(gap_seconds=15, same_participant=True, counted=True, rejoin_number=1),
        source=EventSource.BOT, at_seconds=40)])
    body = exchange(client, issue(client).value).json()
    assert (body["rejoins_counted"], body["rejoins_left"]) == (1, 2)


def test_guessing_passes_is_rate_limited(client):
    codes = [exchange(client, f"v1.rejoin.{IID}:x{i}.9999999999.00").status_code for i in range(8)]
    assert 429 in codes


def test_the_password_and_the_pass_never_appear_in_answers_or_logs(client):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        issued = issue(client)
        r = exchange(client, issued.value)
        exchange(client, issued.value)
    finally:
        logger.remove(sink)
    assert PASSWORD not in r.text and "password" not in r.text.lower()
    logged = "\n".join(lines)
    assert PASSWORD not in logged and issued.value not in logged and r.json()["rejoin_pass"] not in logged
    assert "rejoin pass used" in logged  # something was logged: the search is not empty
