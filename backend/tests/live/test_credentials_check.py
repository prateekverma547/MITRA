"""The early credentials check, join refusing failed interviews, and the shared
rate limits (Session B's C-R1 and C-R2; Session C brief, round 1).

Requirements: the check answers like the join route (401 one answer for an
unknown ID and a wrong password, 409 failed, 410 finished or expired, 503 no slot,
429 too many) and starts no bot, mints no token and changes no state; the round
is returned; the password appears in no log or response; both routes share
limits per address and per meeting ID, and nobody can lock a real candidate out
by failing on purpose.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from app import interviews
from live import credential_limits
from live.credential_limits import CredentialLimits
from tests.live.support import seed_interview

PW = "kmnp3478"
MID = "123-456-789"
IID = "int_check0000001"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/check.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    calls = {"tokens": [], "bots": []}

    async def fake_token(**kw):
        calls["tokens"].append(kw)
        return "daily-token"

    async def fake_start_bot(**kw):
        calls["bots"].append(kw)

    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews, "_start_bot", fake_start_bot)
    credential_limits.limits.reset()
    await db.reset_engine()
    with TestClient(main.app) as c:
        c.calls = calls
        c.portal.call(lambda: seed_interview(IID, status="scheduled", started_ago=None,
                                             meeting_id=MID, password=PW))
        yield c
    await db.reset_engine()
    credential_limits.limits.reset()


def check(client, meeting_id=MID, password=PW):
    return client.post("/interviews/check", json={"meeting_id": meeting_id, "password": password})


def join(client, meeting_id=MID, password=PW):
    return client.post("/interviews/join", json={"meeting_id": meeting_id, "password": password, "consent": True})


def row(client, iid=IID):
    from app import db

    async def read():
        async with db.get_sessionmaker()() as s:
            r = await s.get(db.Interview, iid)
            return {"status": r.status, "consent": r.consent_accepted_at, "tz": r.candidate_timezone,
                    "started": r.started_at}

    return client.portal.call(read)


# -- the check ----------------------------------------------------------------------------


def test_right_credentials_may_join_and_nothing_happens(client):
    before = row(client)
    r = check(client, "123 456 789", PW.upper())  # typed as people type
    assert r.status_code == 200
    body = r.json()
    assert {k: body[k] for k in ("may_join", "round", "state")} == {"may_join": True, "round": "technical", "state": "ready"}
    assert set(body) == {"may_join", "round", "state", "details"}  # details: C-162
    assert client.calls == {"tokens": [], "bots": []}  # no bot, no Daily token
    assert row(client) == before  # no consent, no status, no start time written


def test_unknown_id_and_wrong_password_get_the_same_answer(client):
    wrong = check(client, MID, "wrongpwd")
    unknown = check(client, "999-999-999", PW)
    malformed = check(client, "12", PW)
    assert wrong.status_code == unknown.status_code == malformed.status_code == 401
    assert wrong.json() == unknown.json() == malformed.json() == {"detail": interviews.INVALID_CREDENTIALS}


@pytest.mark.parametrize("status,ended,code", [("completed", 60, 410), ("expired", 60, 410), ("failed", 60, 409)])
def test_finished_and_failed_answer_as_the_join_route_does(client, status, ended, code):
    client.portal.call(lambda: seed_interview("int_check0000002", candidate_id="c2", status=status,
                                             ended_ago=ended, meeting_id="222-333-444", password=PW))
    c, j = check(client, "222-333-444"), join(client, "222-333-444")
    assert c.status_code == j.status_code == code and c.json() == j.json()
    if status == "failed":
        assert c.json()["detail"] == interviews.FAILED_DETAIL
    assert client.calls["bots"] == [] and client.calls["tokens"] == []


def test_an_expired_room_is_410_and_the_check_does_not_record_it(client):
    from app import db

    async def expire():
        async with db.get_sessionmaker()() as s:
            r = await s.get(db.Interview, IID)
            r.room_expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await s.commit()

    client.portal.call(expire)
    assert check(client).status_code == 410
    assert row(client)["status"] == "scheduled"  # the check writes nothing
    assert join(client).status_code == 410
    assert row(client)["status"] == "expired"  # the join records it, as before


def test_no_free_slot_is_503_unless_it_is_a_rejoin(client, monkeypatch):
    monkeypatch.setattr(interviews.registry, "has_capacity", lambda: False)
    r = check(client)
    assert r.status_code == 503 and r.json()["detail"] == interviews.CAPACITY_DETAIL
    client.portal.call(lambda: seed_interview("int_check0000003", candidate_id="c3", meeting_id="333-444-555",
                                             password=PW))  # in progress
    from types import SimpleNamespace

    monkeypatch.setattr(interviews.registry, "_bots", {"int_check0000003": SimpleNamespace(returncode=None)})
    rejoin = check(client, "333-444-555").json()
    assert {k: rejoin[k] for k in ("may_join", "round", "state")} == {"may_join": True, "round": "technical",
                                                                     "state": "in_progress"}


def test_the_password_is_in_no_log_and_no_response(client):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        bodies = [check(client).text, check(client, MID, "wrongpwd").text, join(client).text]
    finally:
        logger.remove(sink)
    assert not any(PW in b for b in bodies)
    assert not any(PW in line for line in lines)


# -- join refuses a failed interview -------------------------------------------------------


def test_join_refuses_a_failed_interview_and_starts_no_bot(client):
    client.portal.call(lambda: seed_interview("int_check0000004", candidate_id="c4", status="failed",
                                             ended_ago=30, meeting_id="444-555-666", password=PW))
    r = join(client, "444-555-666")
    assert r.status_code == 409 and r.json() == {"detail": interviews.FAILED_DETAIL, "reason": "failed",
                                                 "retry_after_seconds": None}  # reason: C-163
    assert client.calls["bots"] == [] and client.calls["tokens"] == []


# -- rate limits ---------------------------------------------------------------------------


def test_per_address_flood_is_refused_on_both_routes_together(client):
    codes = [check(client, MID, "wrongpwd").status_code if i % 2 else
             client.post("/interviews/join", json={"meeting_id": f"9{i:02d}-000-000", "password": "x",
                                                   "consent": True}).status_code
             for i in range(credential_limits.ADDRESS_PER_MINUTE + 5)]
    assert 429 in codes and codes.index(429) <= credential_limits.ADDRESS_PER_MINUTE


def test_one_address_guessing_one_meeting_is_stopped(client):
    for _ in range(credential_limits.PAIR_FAILURES):
        assert check(client, MID, "wrongpwd").status_code == 401
    assert check(client, MID, "wrongpwd").status_code == 429
    assert join(client).status_code == 429  # the join shares the budget


def test_failing_on_purpose_cannot_lock_out_the_real_candidate():
    lim = CredentialLimits()
    now = 1000.0
    # Many addresses fail on the candidate's meeting, past the meeting-wide threshold.
    for n in range(credential_limits.MEETING_FAILURES + 10):
        attacker = f"10.0.{n // 250}.{n % 250}"
        assert lim.refuse(attacker, MID, now) is None
        lim.failed(attacker, MID, now)
    # An attacker address that already failed is now refused for this meeting...
    assert lim.refuse("10.0.0.1", MID, now) == "meeting"
    # ...but the candidate, from an address with no failure on it, is never refused.
    assert lim.refuse("203.0.113.7", MID, now) is None
    # Nor is anyone on another meeting.
    assert lim.refuse("10.0.0.1", "555-666-777", now) is None


def test_the_limits_forget_after_their_windows():
    lim = CredentialLimits()
    for _ in range(credential_limits.PAIR_FAILURES):
        lim.failed("a", MID, 0.0)
    assert lim.refuse("a", MID, 1.0) == "pair"
    assert lim.refuse("a", MID, credential_limits.PAIR_WINDOW + 1.0) is None
