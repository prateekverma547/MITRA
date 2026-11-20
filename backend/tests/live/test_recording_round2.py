"""Recording on (round 2): local storage done safely, and the signed-in route.

Requirements from the brief:
- the storage folder comes from a setting (RECORDINGS_DIR), committed empty;
- Daily's copy is deleted ONLY after the local file is fully written and
  verified (size, and that it opens); a failed download keeps Daily's copy and
  the next sweep retries; retention is unchanged;
- the offset for evidence jumps is the bot's (start acknowledgement), never
  Daily's start_ts;
- a signed-in route serves one interview's recording: seeking (range
  requests), no public address, no caching by shared proxies, every view
  recorded (who, which interview, when); it returns the offset too;
- the console gets the candidate's name without the meeting password.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.live.support import ADMIN_PASSWORD, seed_interview
from tests.test_recordings import a_recording_file

IID = "int_rec2000000001"
STORED = "int_rec2000000002"
PASSWORD = "kmnp3478"
MEETING = "428-193-756"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/rec2.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main, recordings

    disk = tmp_path / "mounted-disk"
    monkeypatch.setattr(recordings, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", tmp_path / "sessions" / "recordings")
    monkeypatch.setenv(recordings.RECORDINGS_DIR_ENV, str(disk))
    a_recording_file(disk / f"{STORED}.mp4", size=50_000)
    await db.reset_engine()
    with TestClient(main.app) as c:
        c.disk = disk
        c.portal.call(lambda: seed_interview(
            IID, status="completed", ended_ago=60, recording_status="recording",
            recording_offset_seconds=6.32, recording_started_at=datetime.now(UTC) - timedelta(minutes=5),
            recording_expires_at=datetime.now(UTC) + timedelta(days=10)))
        c.portal.call(lambda: seed_interview(
            STORED, candidate_id="cand_stored", status="completed", ended_ago=60, recording_status="stored",
            recording_path=f"{STORED}.mp4", recording_bytes=(disk / f"{STORED}.mp4").stat().st_size,
            recording_offset_seconds=6.32, recording_expires_at=datetime.now(UTC) + timedelta(days=9),
            meeting_id=MEETING, password=PASSWORD))
        yield c
    await db.reset_engine()


def _row(client, iid=IID):
    from app import db

    async def read():
        async with db.get_sessionmaker()() as s:
            return await s.get(db.Interview, iid)

    return client.portal.call(read)


def _sweep(client, monkeypatch, download, *, start_ts=0):
    """One real sweep with Daily replaced: records the order of every step."""
    from app import recordings
    from bot.services.daily import DailyRecording

    steps: list[str] = []

    async def find(*, api_key, room_name):
        return DailyRecording(id="daily-rec-9", room_name=room_name, start_ts=start_ts, duration_seconds=120)

    async def fetch(*, api_key, recording_id, destination):
        steps.append("download")
        return download(destination)

    real_verify = recordings.verify_recording_file

    def verify(path, expected):
        problem = real_verify(path, expected)
        steps.append("verified" if problem is None else "verify failed")
        return problem

    async def delete(*, api_key, recording_id):
        steps.append("delete daily copy")

    monkeypatch.setattr(recordings, "find_recording", find)
    monkeypatch.setattr(recordings, "download_recording", fetch)
    monkeypatch.setattr(recordings, "verify_recording_file", verify)
    monkeypatch.setattr(recordings, "delete_recording", delete)
    counts = client.portal.call(lambda: recordings.run_sweep(api_key="test"))
    return counts, steps


def _good(destination):
    a_recording_file(destination, size=9_000)
    return destination.stat().st_size


# -- storage --------------------------------------------------------------------------------------


def test_the_folder_comes_from_the_setting_and_the_row_stores_a_name_only(client, monkeypatch):
    from app import recordings

    counts, steps = _sweep(client, monkeypatch, _good)
    assert counts["stored"] == 1 and steps == ["download", "verified", "delete daily copy"]
    row = _row(client)
    assert row.recording_path == f"{IID}.mp4"  # relative to the folder, so the folder can move
    assert recordings.stored_path(row) == (client.disk / f"{IID}.mp4").resolve()
    assert (client.disk / f"{IID}.mp4").exists()
    assert not (Path(recordings.SESSIONS_DIR) / "recordings" / f"{IID}.mp4").exists()


def test_rows_written_before_the_setting_still_resolve_and_nothing_escapes(client):
    from types import SimpleNamespace

    from app import recordings

    legacy = SimpleNamespace(id="x", recording_path="recordings/old.mp4")
    assert recordings.stored_path(legacy) == (Path(recordings.SESSIONS_DIR) / "recordings" / "old.mp4").resolve()
    for bad in ("../../etc/passwd", "recordings/../../x.mp4", "/etc/passwd"):
        assert recordings.stored_path(SimpleNamespace(id="x", recording_path=bad)) is None


def test_the_setting_is_committed_empty_and_recording_is_on_in_the_example():
    text = (REPO / ".env.example").read_text()
    assert re.search(r"^RECORDINGS_DIR=$", text, re.M)  # a setting, no value committed
    assert re.search(r"^RECORDING_ENABLED=1$", text, re.M)  # decided on for technical rounds


# -- Daily's copy goes only after ours is verified --------------------------------------------------


@pytest.mark.parametrize("name,download", [
    ("truncated", lambda d: (a_recording_file(d, size=9_000),
                             d.write_bytes(d.read_bytes()[:-500]), d.stat().st_size + 500)[2]),
    ("cut inside a box", lambda d: (a_recording_file(d, size=9_000),
                                    d.write_bytes(d.read_bytes()[:-500]), d.stat().st_size)[2]),
    ("not an mp4", lambda d: (d.parent.mkdir(parents=True, exist_ok=True),
                              d.write_bytes(b"<html>expired link</html>"), d.stat().st_size)[2]),
    ("no index", lambda d: (d.parent.mkdir(parents=True, exist_ok=True),
                            d.write_bytes((24).to_bytes(4, "big") + b"ftypmp42" + b"\0" * 12
                                          + (12).to_bytes(4, "big") + b"mdatxxxx"), d.stat().st_size)[2]),
])
def test_a_bad_download_keeps_dailys_copy_removes_ours_and_is_retried(client, monkeypatch, name, download):
    counts, steps = _sweep(client, monkeypatch, download)
    assert counts["error"] == 1, name
    assert steps == ["download", "verify failed"]  # Daily's copy untouched
    row = _row(client)
    assert row.recording_status == "recording" and row.recording_path is None
    assert not (client.disk / f"{IID}.mp4").exists()
    # The next sweep tries again, and this time the file is whole.
    counts, steps = _sweep(client, monkeypatch, _good)
    assert counts["stored"] == 1 and steps == ["download", "verified", "delete daily copy"]


def test_a_failed_download_never_reaches_verification_or_deletion(client, monkeypatch):
    def fails(destination):
        raise RuntimeError("connection reset")

    counts, steps = _sweep(client, monkeypatch, fails)
    assert counts["error"] == 1 and steps == ["download"]
    assert _row(client).recording_status == "recording"


def test_retention_still_deletes_a_recording_that_never_verified(client, monkeypatch):
    """A file that will not verify is never stored, but Daily's copy still goes at
    the retention deadline, set when recording started (unchanged sweep)."""
    from app import db, recordings

    async def age():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, IID)
            row.recording_id = "daily-rec-9"
            row.recording_expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await s.commit()

    client.portal.call(age)
    counts, steps = _sweep(client, monkeypatch, lambda d: (d.parent.mkdir(parents=True, exist_ok=True),
                                                           d.write_bytes(b"junk"), 4)[2])
    assert counts["expired"] == 1 and steps == ["download", "verify failed", "delete daily copy"]
    assert _row(client).recording_status == "expired"
    assert recordings.RECORDING_RETENTION_DAYS == 10


# -- the offset ------------------------------------------------------------------------------------


def test_the_sweep_never_replaces_the_bots_offset_with_dailys_start_ts(client, monkeypatch):
    before = _row(client).recording_offset_seconds
    started = _row(client).recording_started_at.replace(tzinfo=UTC)
    # Daily's start_ts as D measured it: whole seconds, 2.3 s early.
    counts, _ = _sweep(client, monkeypatch, _good, start_ts=int(started.timestamp()) - 3)
    assert counts["stored"] == 1
    assert _row(client).recording_offset_seconds == before == 6.32


# -- the signed-in route -----------------------------------------------------------------------------


def _views(client):
    from sqlalchemy import select

    from app import db
    from live.models import RecordingViewRecord

    async def read():
        async with db.get_sessionmaker()() as s:
            return list(await s.scalars(select(RecordingViewRecord).order_by(RecordingViewRecord.id)))

    return client.portal.call(read)


MEDIA = f"/api/live/interviews/{STORED}/recording/media"


def test_the_route_needs_sign_in_and_records_nothing_without_it(client):
    assert client.get(MEDIA).status_code == 401
    assert client.get(f"/api/live/interviews/{STORED}/recording").status_code == 401
    assert _views(client) == []


def test_the_route_seeks_is_never_cached_by_a_shared_proxy_and_gives_the_offset(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    whole = (client.disk / f"{STORED}.mp4").read_bytes()
    full = client.get(MEDIA)
    assert full.status_code == 200 and full.content == whole
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["cache-control"] == "private, no-store"
    assert full.headers["x-recording-offset-seconds"] == "6.32"
    assert full.headers["accept-ranges"] == "bytes"
    for header, start, end in (("bytes=1000-1999", 1000, 1999), ("bytes=40000-", 40000, len(whole) - 1),
                               ("bytes=-100", len(whole) - 100, len(whole) - 1)):
        part = client.get(MEDIA, headers={"Range": header})
        assert part.status_code == 206, header
        assert part.content == whole[start:end + 1]
        assert part.headers["content-range"] == f"bytes {start}-{end}/{len(whole)}"
        assert part.headers["cache-control"] == "private, no-store"


def test_every_view_is_recorded_with_who_which_interview_and_when(client):
    from loguru import logger

    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        client.get(MEDIA, headers={"Range": "bytes=0-"})
        client.get(MEDIA, headers={"Range": "bytes=30000-31000"})
        client.get(f"/interviews/{STORED}/recording/video")  # the old panel's player too
    finally:
        logger.remove(sink)
    views = _views(client)
    assert [(v.interview_id, v.viewer, v.route, v.byte_range) for v in views] == [
        (STORED, "admin", "console", "bytes=0-"), (STORED, "admin", "console", "bytes=30000-31000"),
        (STORED, "admin", "panel", None)]
    assert all(abs((v.viewed_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()) < 60 for v in views)
    assert sum(f"[{STORED}] recording viewed by admin" in line for line in lines) == 3


def test_the_info_route_gives_the_offset_and_a_plain_signed_in_address(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    info = client.get(f"/api/live/interviews/{STORED}/recording").json()
    assert info["media_url"] == MEDIA and "?" not in info["media_url"]
    assert info["offset_seconds"] == 6.32 and info["offset_known"] is True
    assert info["status"] == "stored" and info["bytes"] == (client.disk / f"{STORED}.mp4").stat().st_size
    assert info["expires_at"] is not None
    waiting = client.get(f"/api/live/interviews/{IID}/recording").json()
    assert waiting["status"] == "recording" and waiting["media_url"] is None and waiting["bytes"] is None
    assert _views(client) == []  # reading the info is not a view


def test_neither_route_ever_carries_the_meeting_password(client):
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    for path in (f"/api/live/interviews/{STORED}/recording", f"/api/live/interviews/{STORED}/header"):
        r = client.get(path)
        assert r.status_code == 200 and PASSWORD not in r.text and MEETING not in r.text


# -- the candidate's name for the console --------------------------------------------------------------


def test_the_header_gives_the_name_and_never_the_password(client):
    assert client.get(f"/api/live/interviews/{STORED}/header").status_code == 401
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    r = client.get(f"/api/live/interviews/{STORED}/header")
    assert r.json() == {"schema_version": 1, "interview_id": STORED, "candidate_id": "cand_stored",
                        "candidate_name": "Test Candidate", "round": "technical", "status": "completed",
                        "booked_duration_minutes": None}
    assert "password" not in r.text.lower() and PASSWORD not in r.text
    assert client.get("/api/live/interviews/int_nope/header").status_code == 404


# -- the shutdown limit and CI ---------------------------------------------------------------------------


def test_the_start_command_limits_how_long_open_streams_hold_a_shutdown():
    cmd = next(line for line in (REPO / "backend" / "Dockerfile").read_text().splitlines()
               if line.startswith("CMD"))
    match = re.search(r"--timeout-graceful-shutdown (\d+)", cmd)
    assert match and 1 <= int(match.group(1)) <= 10, cmd


def test_ci_checks_the_console_like_the_other_frontend_packages():
    import yaml

    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    steps = [(s.get("working-directory"), s.get("run", "")) for s in jobs["frontend"]["steps"]]
    for command in ("npm ci", "npm run typecheck", "npm test", "npx vite build"):
        assert ("frontend/console", command) in steps, command
