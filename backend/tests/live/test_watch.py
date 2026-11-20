"""The hidden watcher join (task 22, backend; decision 7).

From the brief: admin sign-in for now; refuse finished, failed and HR rounds;
record every watch; the token hidden (hasPresence false), unable to send, not an
owner, expiring at the hard stop plus a margin with eject; no meeting password.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from live import watch
from tests.live.support import ADMIN_PASSWORD, seed_interview

IID = "int_watch0000001"


# -- the token's properties, as posted to Daily ------------------------------------------


class _FakeResponse:
    status = 200

    async def json(self):
        return {"token": "watcher-token"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    posted: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        _FakeSession.posted.append({"url": url, "json": json})
        return _FakeResponse()


async def test_the_watcher_token_is_hidden_receive_only_not_an_owner_and_ejected_at_expiry(monkeypatch):
    from bot.services import daily

    _FakeSession.posted = []
    monkeypatch.setattr(daily.aiohttp, "ClientSession", _FakeSession)
    token = await daily.create_watcher_token(api_key="k", room_name="room-x",
                                             user_id="watcher:admin", expires_at=1_900_000_000)
    assert token == "watcher-token"
    props = _FakeSession.posted[0]["json"]["properties"]
    assert _FakeSession.posted[0]["url"].endswith("/meeting-tokens")
    assert props["room_name"] == "room-x"
    assert props["is_owner"] is False
    assert props["permissions"]["hasPresence"] is False
    assert props["permissions"]["canSend"] is False
    assert props["permissions"]["canAdmin"] is False
    assert props["enable_screenshare"] is False
    assert props["start_video_off"] is True and props["start_audio_off"] is True
    assert props["exp"] == 1_900_000_000 and props["eject_at_token_exp"] is True
    assert props["user_id"] == "watcher:admin"


async def test_a_user_id_over_36_characters_is_refused():
    from bot.services import daily

    with pytest.raises(ValueError):
        await daily.create_watcher_token(api_key="k", room_name="r", user_id="watcher:" + "x" * 29,
                                         expires_at=1)
    with pytest.raises(ValueError):
        watch.watcher_user_id("x" * 29)
    assert len(watch.watcher_user_id("usr_0123456789ab")) <= 36


# -- the rule ---------------------------------------------------------------------------


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def interview(**kw):
    base = dict(status="in_progress", started_at=NOW - timedelta(minutes=5),
                room_expires_at=NOW + timedelta(hours=20), planned_duration_minutes=None,
                planned_grace_minutes=None)
    return SimpleNamespace(**{**base, **kw})


REFUSALS = [
    ({"status": "completed"}, "finished", "finished"),
    ({"status": "expired"}, "finished", "finished"),
    ({"status": "failed"}, "failed", "failed"),
    ({"status": "scheduled", "started_at": None}, "Not started yet", "not_started"),
    ({"round": "hr"}, "HR rounds", "hr_round"),
    ({"started_at": NOW - timedelta(minutes=200)}, "past its hard stop", "past_hard_stop"),
]


@pytest.mark.parametrize("fields,phrase,reason", REFUSALS)
def test_refusals(fields, phrase, reason):
    with pytest.raises(watch.WatchRefused) as caught:
        watch.watch_pass(interview(**fields), None, who="admin", now=NOW)
    assert caught.value.status == 409 and phrase in str(caught.value)
    # B-50: a code the console switches on, not only a message.
    assert caught.value.reason == reason


def test_every_reason_code_is_produced_by_some_refusal():
    from shared.interview_contracts import WatchRefusalReason

    assert {r for _, _, r in REFUSALS} == {r.value for r in WatchRefusalReason}


def test_expiry_is_the_hard_stop_plus_the_margin():
    started = NOW - timedelta(minutes=5)
    booked = watch.watch_pass(interview(planned_duration_minutes=56, planned_grace_minutes=2), None,
                              who="admin", now=NOW)
    assert booked.expires_at == started + timedelta(minutes=56 + 2 + watch.WATCH_MARGIN_MINUTES)
    plan = {"evaluation_spec": {"duration_minutes": 30, "overrun_grace_minutes": 5}}
    assert watch.watch_pass(interview(), plan, who="admin", now=NOW).expires_at == started + timedelta(minutes=45)
    capped = watch.watch_pass(interview(room_expires_at=NOW + timedelta(minutes=3)), None, who="a", now=NOW)
    assert capped.expires_at == NOW + timedelta(minutes=3)  # never past the room's own expiry


# -- the route ---------------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/watch.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main
    from bot.services import daily

    issued: list = []

    async def fake_watcher_token(**kw):
        issued.append(kw)
        return "watcher-token-value"

    monkeypatch.setattr(daily, "create_watcher_token", fake_watcher_token)
    await db.reset_engine()
    with TestClient(main.app) as c:
        c.issued = issued
        c.portal.call(lambda: seed_interview(IID, started_ago=120.0, password="pw-secret-8"))
        yield c
    await db.reset_engine()


def records(client):
    from sqlalchemy import select

    from app import db
    from live.models import WatchRecord

    async def read():
        async with db.get_sessionmaker()() as s:
            return list(await s.scalars(select(WatchRecord)))

    return client.portal.call(read)


def test_the_route_needs_the_admin_session(client):
    assert client.post(f"/api/live/interviews/{IID}/watch").status_code == 401
    assert records(client) == []


def test_watching_returns_the_room_and_a_watcher_token_and_records_it(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    r = client.post(f"/api/live/interviews/{IID}/watch")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room_url"] == f"https://example.daily.co/room-{IID}"
    assert body["token"] == "watcher-token-value" and body["watcher_user_id"] == "watcher:admin"
    assert body["hidden"] is True and body["can_send"] is False
    assert "pw-secret-8" not in r.text and "password" not in body  # never the meeting password
    issued = client.issued[0]
    assert issued["room_name"] == f"room-{IID}" and issued["user_id"] == "watcher:admin"
    [rec] = records(client)
    assert (rec.interview_id, rec.watcher, rec.daily_user_id) == (IID, "admin", "watcher:admin")
    assert int(rec.token_expires_at.replace(tzinfo=UTC).timestamp()) == issued["expires_at"]


@pytest.mark.parametrize("status,ended", [("completed", 60), ("failed", 60), ("expired", 60)])
def test_finished_and_failed_interviews_are_refused_and_not_recorded(client, status, ended):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    iid = f"int_watchdone{status[:3]}"
    client.portal.call(lambda: seed_interview(iid, candidate_id=f"c_{status}", status=status, ended_ago=ended))
    r = client.post(f"/api/live/interviews/{iid}/watch")
    assert r.status_code == 409
    # The message as before, plus the reason code (B-50): exactly these two keys.
    expected = "failed" if status == "failed" else "finished"
    assert r.json().keys() == {"detail", "reason"} and r.json()["reason"] == expected
    assert records(client) == [] and client.issued == []


def test_a_not_started_interview_is_refused_with_its_code(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    client.portal.call(lambda: seed_interview("int_watchwait001", candidate_id="c_wait", status="scheduled",
                                             started_ago=None))
    r = client.post("/api/live/interviews/int_watchwait001/watch")
    assert r.status_code == 409
    assert r.json() == {"detail": "Not started yet: watching begins once the candidate has joined.",
                        "reason": "not_started"}


def test_unknown_interview_is_404(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    assert client.post("/api/live/interviews/int_nope/watch").status_code == 404


# -- hidden watchers are never in the recording (D-04 measured it; this keeps it true) ----


async def test_a_watcher_token_never_allows_recording_or_being_recorded_visibly(monkeypatch):
    """Daily's cloud recording composites what participants send and lays out who
    is present. A watcher stays out of it only while it is hidden (hasPresence
    false), sends nothing (canSend false), is not an owner (owners can start and
    stop recordings) and has no recording property at all. The property set is
    checked whole, so a recording-related property added later fails here."""
    from bot.services import daily

    _FakeSession.posted = []
    monkeypatch.setattr(daily.aiohttp, "ClientSession", _FakeSession)
    await daily.create_watcher_token(api_key="k", room_name="room-x", user_id="watcher:admin",
                                     expires_at=1_900_000_000)
    props = _FakeSession.posted[0]["json"]["properties"]
    assert set(props) == {"room_name", "exp", "eject_at_token_exp", "is_owner", "user_id", "user_name",
                          "enable_screenshare", "start_video_off", "start_audio_off", "permissions"}
    assert not any("record" in key.lower() for key in props)
    assert props["is_owner"] is False
    assert props["permissions"] == {"hasPresence": False, "canSend": False, "canAdmin": False}
    assert props["enable_screenshare"] is False
