"""Evidence links: known keys only, this interview's keys only, behind sign-in.

Round 2: a link opens the signed-in recording route; the token address that
needed no sign-in (/api/live/evidence/media/<token>) is gone."""

import pytest
from fastapi.testclient import TestClient

from live import evidence
from tests.live.support import ADMIN_PASSWORD, seed_interview

IID = "int_evid00000001"
OTHER = "int_evid00000002"
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40


def test_key_scheme():
    assert evidence.recording_key(IID, 12.3456) == f"recording/{IID}/at/12.346"
    parsed = evidence.parse_key(f"recording/{IID}/at/12.5", for_interview=IID)
    assert (parsed.scheme, parsed.interview_id, parsed.seconds) == ("recording", IID, 12.5)


@pytest.mark.parametrize("key,status", [
    (f"recording/{OTHER}/at/12", 404),          # another interview's key
    (f"thumbnail/{IID}/frame_001.jpg", 404),    # reserved, not stored yet
    (f"clip/{IID}/10-20", 404),                 # reserved, not stored yet
    (f"https://evil.example/{IID}", 400),       # a URL is not a key
    (f"recording/{IID}/at/-3", 400),            # malformed
    (f"recording/{IID}/at/12/../../x", 400),    # path tricks
    ("", 400),
])
def test_bad_keys_are_refused(key, status):
    with pytest.raises(evidence.EvidenceKeyError) as caught:
        evidence.parse_key(key, for_interview=IID)
    assert caught.value.status == status


def test_video_time_subtracts_the_measured_offset():
    assert evidence.video_seconds(100.0, 6.32) == 93.68
    assert evidence.video_seconds(3.0, 6.32) == 0.0  # before the recording began
    assert evidence.video_seconds(100.0, None) == 100.0


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/evid.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main, recordings

    sessions = tmp_path / "sessions"
    (sessions / "recordings").mkdir(parents=True)
    (sessions / "recordings" / f"{IID}.mp4").write_bytes(VIDEO)
    monkeypatch.setattr(recordings, "SESSIONS_DIR", sessions)
    monkeypatch.delenv(recordings.RECORDINGS_DIR_ENV, raising=False)
    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(lambda: seed_interview(IID, status="completed", ended_ago=60,
                                             recording_status="stored",
                                             recording_path=f"recordings/{IID}.mp4",
                                             recording_offset_seconds=6.32))
        c.portal.call(lambda: seed_interview(OTHER, candidate_id="cand_other", status="completed",
                                             ended_ago=60))
        yield c
    await db.reset_engine()


def link(client, key, interview=IID):
    return client.get(f"/api/live/interviews/{interview}/evidence-link", params={"key": key})


def test_the_link_route_needs_the_admin_session(client):
    assert link(client, f"recording/{IID}/at/30").status_code == 401


def test_a_recording_moment_opens_at_the_right_second(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    r = link(client, f"recording/{IID}/at/30")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_seconds"] == 23.68 and body["offset_known"] is True
    url = body["url"]
    assert url == f"/api/live/interviews/{IID}/recording/media#t=23.68"  # signed in, no token
    media = client.get(url.split("#")[0])
    assert media.status_code == 200 and media.content == VIDEO
    ranged = client.get(url.split("#")[0], headers={"Range": "bytes=10-19"})
    assert ranged.status_code == 206 and ranged.content == VIDEO[10:20]
    client.cookies.clear()  # without the sign-in the same address serves nothing
    assert client.get(url.split("#")[0]).status_code == 401


def test_another_interviews_key_is_refused(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    assert link(client, f"recording/{OTHER}/at/30").status_code == 404
    assert link(client, f"recording/{IID}/at/30", interview=OTHER).status_code == 404
    assert link(client, "nonsense").status_code == 400


def test_no_stored_recording_means_no_link(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    r = link(client, f"recording/{OTHER}/at/30", interview=OTHER)
    assert r.status_code == 404 and "not_recorded" in r.json()["detail"]


def test_the_token_address_that_needed_no_sign_in_is_gone(client):
    assert client.get("/api/live/evidence/media/v1.evidence.x.9999999999.deadbeef").status_code == 404
    from app.main import app

    assert not any("/evidence/media" in getattr(r, "path", "") for r in app.routes)
