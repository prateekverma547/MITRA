"""Recording an interview, keeping it for ten days, and deleting it for real.

Daily is stubbed throughout. What is under test is our half: that a recording
which will not start cannot end an interview, that the reference survives to be
read back, that deleting removes the bytes and not just the row, that an
interview with no recording says so, and that ten days is something that runs
rather than something written down.

The last two are the ones worth having. A retention policy nothing enforces is a
comment, and a delete button that clears a row while the file stays is the exact
failure this codebase has now recorded six times.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from shared.contracts import RECORDING_RETENTION_DAYS


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/rec.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    # The sweep is driven by hand here so its behaviour can be watched. A loop
    # running underneath would make calls nobody asked for.
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")

    from app import db, main, recordings

    await db.reset_engine()
    # Recordings land under the test's own directory, never the real one.
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", tmp_path / "sessions" / "recordings")
    monkeypatch.setattr(recordings, "SESSIONS_DIR", tmp_path / "sessions")

    with TestClient(main.app) as test_client:
        test_client.post("/admin/login", json={"password": "test-admin-pw"})
        yield test_client

    await db.reset_engine()


async def make_interview(**overrides) -> str:
    """One completed interview, with whatever recording state the test needs."""
    from app import db

    async with db.get_sessionmaker()() as session:
        session.add(db.Job(id="job_1", jd_text="jd", spec_status=db.SpecStatus.READY))
        await session.commit()
    async with db.get_sessionmaker()() as session:
        session.add(
            db.Candidate(
                id="cand_1",
                job_id="job_1",
                name="Prateek Verma",
                cv_text="cv",
                blueprint_status=db.BlueprintStatus.READY,
            )
        )
        await session.commit()
    async with db.get_sessionmaker()() as session:
        fields = {
            "id": "int_rec1",
            "candidate_id": "cand_1",
            "meeting_id": "111-222-333",
            "password": "pw",
            "daily_room_name": "room-abc",
            "daily_room_url": "https://example.daily.co/room-abc",
            "room_expires_at": datetime.now(UTC) + timedelta(hours=24),
            "status": db.InterviewStatus.COMPLETED,
            "ended_at": datetime.now(UTC),
            "transcript": {
                "interview_id": "int_rec1",
                "turns": [],
                "duration_seconds": 60.0,
            },
        }
        fields.update(overrides)
        session.add(db.Interview(**fields))
        await session.commit()
    return fields["id"]


def a_recording_file(path, size=2048):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * size)
    return path


# -- a recording that will not start must not end the interview --------------


class RefusingTransport:
    async def start_recording(self, *a, **k):
        raise RuntimeError("Daily said no")


class HangingTransport:
    async def start_recording(self, *a, **k):
        import asyncio

        await asyncio.sleep(30)


async def test_a_refused_recording_start_does_not_raise():
    """`start_recording` is called from an event handler in the middle of a live
    call. Anything it raises would surface there, and the interview is worth more
    than the recording."""
    from bot.services.daily import start_recording

    reason = await start_recording(RefusingTransport())

    assert reason and "Daily said no" in reason


async def test_a_hanging_recording_start_gives_up_rather_than_blocking(monkeypatch):
    """A video service that never answers must not hold an interview open."""
    from bot.services import daily

    monkeypatch.setattr(daily, "RECORDING_START_TIMEOUT_SECONDS", 0.2)
    reason = await daily.start_recording(HangingTransport())

    assert reason and "did not acknowledge" in reason


async def test_the_interview_keeps_running_when_the_recording_fails(client):
    """The whole point. The interview stays in progress, the reason is written
    down, and nothing raises out of the handler."""
    from app import db
    from bot.run_bot import begin_recording

    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)

    recording_on = await begin_recording(
        RefusingTransport(), interview_id=interview_id, session_id="s", clock_zero=0.0
    )

    assert recording_on is False
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.status == db.InterviewStatus.IN_PROGRESS, (
            "a failed recording ended the interview"
        )
        assert interview.recording_status == db.RecordingStatus.UNAVAILABLE
        assert "Daily said no" in interview.recording_error


# -- the reference is persisted and readable ---------------------------------


class WillingTransport:
    async def start_recording(self, *a, **k):
        return ("stream-1", None)


async def test_the_recording_reference_is_written_while_the_call_is_live(client):
    """Mid-session, not in the `finally` block.

    A killed process never reaches its `finally`, and a recording nobody has
    recorded is one nobody collects and therefore one nobody ever deletes. That
    is a retention promise quietly broken by a crash.
    """
    from app import db
    from bot.run_bot import begin_recording

    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)

    assert await begin_recording(
        WillingTransport(), interview_id=interview_id, session_id="s", clock_zero=0.0
    )

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING
        assert interview.recording_started_at is not None
        assert interview.recording_expires_at is not None
        assert interview.recording_offset_seconds is not None


async def test_the_retention_deadline_is_set_from_the_interview_not_from_access(client):
    """The clock starts at the interview. A window that reset whenever somebody
    opened the video would not be the promise the candidate was shown."""
    from app import db
    from bot.run_bot import begin_recording

    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)
    await begin_recording(
        WillingTransport(), interview_id=interview_id, session_id="s", clock_zero=0.0
    )

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        started = interview.recording_started_at.replace(tzinfo=UTC)
        expires = interview.recording_expires_at.replace(tzinfo=UTC)

    assert abs((expires - started).days - RECORDING_RETENTION_DAYS) < 1


async def test_the_panel_can_read_the_recording_back(client):
    """It has to reach the reviewer, not merely reach the database."""
    from app import db

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
        recording_bytes=2048,
        recording_offset_seconds=1.5,
        recording_expires_at=datetime.now(UTC) + timedelta(days=RECORDING_RETENTION_DAYS),
    )

    body = client.get(f"/interviews/{interview_id}").json()

    assert body["recording_status"] == "stored"
    assert body["recording_offset_seconds"] == 1.5
    assert body["recording_bytes"] == 2048
    assert body["recording_expires_at"]


async def test_the_video_is_served_from_our_own_disk(client, tmp_path):
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
    )
    a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")

    response = client.get(f"/interviews/{interview_id}/recording/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert b"ftypmp42" in response.content


async def test_seeking_works_because_the_video_answers_range_requests(client):
    """Clicking a transcript line is worthless if the browser has to download
    the whole file to reach the moment."""
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
    )
    a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")

    response = client.get(
        f"/interviews/{interview_id}/recording/video", headers={"Range": "bytes=10-19"}
    )

    assert response.status_code == 206
    assert len(response.content) == 10


async def test_the_recording_is_admin_only_like_everything_else(client):
    from app import db

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
    )
    client.post("/admin/logout")

    assert client.get(f"/interviews/{interview_id}/recording/video").status_code == 401
    assert client.delete(f"/interviews/{interview_id}/recording").status_code == 401


# -- an interview with no recording says so ----------------------------------


async def test_no_recording_is_a_sentence_not_a_broken_player(client):
    """Every state the panel branches on has to survive the round trip, or the
    page falls through to a player pointing at nothing."""
    from app import db

    interview_id = await make_interview()

    body = client.get(f"/interviews/{interview_id}").json()

    assert body["recording_status"] == "not_recorded"
    assert body["recording_bytes"] is None


async def test_playing_a_recording_that_is_not_there_explains_itself(client):
    """The row says stored and the disk disagrees. Somebody has to be told which."""
    from app import db

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
    )

    response = client.get(f"/interviews/{interview_id}/recording/video")

    assert response.status_code == 404
    assert "no recording file" in response.json()["detail"]


async def test_the_reason_there_is_no_recording_reaches_the_panel(client):
    from app import db

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.UNAVAILABLE,
        recording_error="Daily did not acknowledge the recording.",
    )

    body = client.get(f"/interviews/{interview_id}").json()

    assert body["recording_status"] == "unavailable"
    assert "did not acknowledge" in body["recording_error"]


def test_the_panel_has_a_branch_for_every_recording_state():
    """There is no JS harness, so this reads the file.

    A state the panel does not handle falls through to whatever the last branch
    was, and the last branch here renders a player.
    """
    from pathlib import Path

    from app.db import RecordingStatus

    panel = (
        Path(__file__).resolve().parents[2] / "frontend" / "admin" / "admin.js"
    ).read_text()
    card = panel[panel.index("function recordingCard"):panel.index("function wireRecording")]

    for state in RecordingStatus:
        assert f'"{state.value}"' in card or state is RecordingStatus.NOT_RECORDED, (
            f"the panel has nothing to say about a recording that is '{state.value}'"
        )
    assert "<video" in card
    # The player is inside the stored branch only.
    assert card.index("<video") > card.index('=== "stored"')


# -- deleting removes the file, not merely the row ---------------------------


async def test_deleting_removes_the_bytes(client, monkeypatch):
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
        recording_id="daily-rec-1",
    )
    path = a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")
    deleted_at_daily = []

    async def fake_delete(*, api_key, recording_id):
        deleted_at_daily.append(recording_id)

    monkeypatch.setattr(recordings, "delete_recording", fake_delete)

    response = client.delete(f"/interviews/{interview_id}/recording")

    assert response.status_code == 200
    assert not path.exists(), "the row was cleared but the file is still on disk"
    assert deleted_at_daily == ["daily-rec-1"], "Daily's copy was never deleted"

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.DELETED
        assert interview.recording_deleted_at is not None
        assert interview.recording_path is None
        # The transcript is not collateral.
        assert interview.transcript is not None


async def test_a_deletion_that_fails_says_so_rather_than_clearing_the_row(client, monkeypatch):
    """The failure this whole change is written against, from the other side.

    If Daily will not confirm, the recording may still exist there. Reporting
    success would tell a reviewer, and through them a candidate, that something
    is gone when it is not.
    """
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
        recording_id="daily-rec-1",
    )
    a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")

    async def refuses(*, api_key, recording_id):
        raise RuntimeError("Daily did not confirm the deletion")

    monkeypatch.setattr(recordings, "delete_recording", refuses)

    response = client.delete(f"/interviews/{interview_id}/recording")

    assert response.status_code == 502
    assert "still there" in response.json()["detail"]
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.STORED, (
            "the row was marked deleted even though the deletion failed"
        )


async def test_there_is_nothing_to_delete_twice(client, monkeypatch):
    from app import db, recordings

    interview_id = await make_interview(recording_status=db.RecordingStatus.DELETED)

    assert client.delete(f"/interviews/{interview_id}/recording").status_code == 409


# -- ten days is enforced ----------------------------------------------------


async def test_the_sweep_deletes_a_recording_past_its_window(client, monkeypatch):
    """The test that makes retention a mechanism instead of a sentence."""
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
        recording_id="daily-rec-1",
        recording_started_at=datetime.now(UTC) - timedelta(days=RECORDING_RETENTION_DAYS + 1),
        recording_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    path = a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")

    async def fake_delete(*, api_key, recording_id):
        return None

    monkeypatch.setattr(recordings, "delete_recording", fake_delete)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["expired"] == 1
    assert not path.exists(), "the retention sweep left the file on disk"
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.EXPIRED
        # The interview record survives its recording.
        assert interview.transcript is not None


async def test_the_sweep_leaves_a_recording_inside_its_window_alone(client, monkeypatch):
    from app import db, recordings

    await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
        recording_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    path = a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")

    counts = await recordings.run_sweep(api_key="test")

    assert counts["expired"] == 0
    assert path.exists()


async def test_a_recording_that_never_reached_us_is_still_deleted_at_daily(client, monkeypatch):
    """The bot was killed, so no file was ever downloaded. Daily still has one,
    and the ten days applies to it just the same."""
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.RECORDING,
        recording_id="daily-rec-1",
        recording_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    deleted = []

    async def fake_delete(*, api_key, recording_id):
        deleted.append(recording_id)

    async def no_recording(*, api_key, room_name):
        return None

    monkeypatch.setattr(recordings, "delete_recording", fake_delete)
    monkeypatch.setattr(recordings, "find_recording", no_recording)

    await recordings.run_sweep(api_key="test")

    assert deleted == ["daily-rec-1"]
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.EXPIRED


async def test_a_retention_deletion_that_fails_is_retried_not_forgotten(client, monkeypatch):
    """Giving up on a deletion is how a ten day promise becomes an archive."""
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_path="recordings/int_rec1.mp4",
        recording_id="daily-rec-1",
        recording_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    a_recording_file(recordings.RECORDINGS_DIR / "int_rec1.mp4")

    async def refuses(*, api_key, recording_id):
        raise RuntimeError("Daily is down")

    monkeypatch.setattr(recordings, "delete_recording", refuses)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["error"] == 1
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.STORED, (
            "a failed deletion left the interview in a state the sweep will not revisit"
        )


def test_the_retention_number_has_exactly_one_source():
    """The candidate is promised a number and a job enforces one. If those are
    typed in two places they will eventually disagree, and the disagreement will
    be silent."""
    from pathlib import Path

    from app.main import _render_page

    repo = Path(__file__).resolve().parents[2]
    for page in ("candidate/index.html", "admin/admin.js"):
        source = (repo / "frontend" / page).read_text()
        assert "{{RETENTION_DAYS}}" in source, f"{page} does not read the constant"
        rendered = _render_page(page)
        assert "{{RETENTION_DAYS}}" not in rendered, "a token reached the browser"
        assert str(RECORDING_RETENTION_DAYS) in rendered


# -- collecting from Daily ---------------------------------------------------


async def test_collection_downloads_then_deletes_and_never_the_other_way(client, monkeypatch):
    """Order matters. Deleting Daily's copy before ours is verified would mean a
    failed download destroys the only copy there is."""
    from app import db, recordings
    from bot.services.daily import DailyRecording

    interview_id = await make_interview(recording_status=db.RecordingStatus.RECORDING)
    order = []

    async def fake_find(*, api_key, room_name):
        return DailyRecording(id="daily-rec-1", room_name=room_name, start_ts=0, duration_seconds=60)

    async def fake_download(*, api_key, recording_id, destination):
        order.append("download")
        a_recording_file(destination)
        return destination.stat().st_size

    async def fake_delete(*, api_key, recording_id):
        order.append("delete")

    monkeypatch.setattr(recordings, "find_recording", fake_find)
    monkeypatch.setattr(recordings, "download_recording", fake_download)
    monkeypatch.setattr(recordings, "delete_recording", fake_delete)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["stored"] == 1
    assert order == ["download", "delete"]
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.STORED
        assert interview.recording_bytes > 0
        assert recordings.stored_path(interview).exists()


async def test_a_failed_download_keeps_dailys_copy_and_tries_again(client, monkeypatch):
    from app import db, recordings
    from bot.services.daily import DailyRecording

    interview_id = await make_interview(recording_status=db.RecordingStatus.RECORDING)
    deleted = []

    async def fake_find(*, api_key, room_name):
        return DailyRecording(id="daily-rec-1", room_name=room_name, start_ts=0, duration_seconds=60)

    async def fails(*, api_key, recording_id, destination):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(recordings, "find_recording", fake_find)
    monkeypatch.setattr(recordings, "download_recording", fails)
    monkeypatch.setattr(recordings, "delete_recording",
                        lambda **k: deleted.append(k["recording_id"]))

    counts = await recordings.run_sweep(api_key="test")

    assert counts["error"] == 1
    assert deleted == [], "Daily's copy was deleted after our download failed"
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING


async def test_a_recording_that_never_arrives_is_eventually_called_missing(client, monkeypatch):
    """Otherwise the panel says "still being prepared" for ever."""
    from app import db, recordings

    interview_id = await make_interview(
        recording_status=db.RecordingStatus.RECORDING,
        ended_at=datetime.now(UTC) - timedelta(hours=recordings.COLLECT_GIVE_UP_HOURS + 1),
    )

    async def nothing(*, api_key, room_name):
        return None

    monkeypatch.setattr(recordings, "find_recording", nothing)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["unavailable"] == 1
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.UNAVAILABLE
        assert interview.recording_error


async def test_a_live_interview_is_not_declared_missing(client, monkeypatch):
    """Daily produces nothing until the call ends, so "not there yet" during an
    interview is the normal case and must not be read as a failure."""
    from app import db, recordings

    interview_id = await make_interview(
        status=db.InterviewStatus.IN_PROGRESS,
        ended_at=None,
        recording_status=db.RecordingStatus.RECORDING,
    )

    async def nothing(*, api_key, room_name):
        return None

    monkeypatch.setattr(recordings, "find_recording", nothing)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["waiting"] == 1
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING


# -- the room, and what the candidate is told --------------------------------


def test_the_room_permits_recording():
    import inspect

    from bot.services import daily

    assert '"enable_recording": "cloud"' in inspect.getsource(daily)


def test_stopping_is_not_the_only_thing_that_ends_a_recording():
    """The `finally` block does not run when a process is killed. Daily closing
    the recording when the room empties is what covers a redeploy or an OOM kill,
    and it was verified against the live API rather than assumed."""
    import inspect

    from bot.services import daily

    source = inspect.getsource(daily.stop_recording)
    assert "room empties" in source or "last participant leaves" in source


def test_the_consent_notice_says_what_now_happens():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2] / "frontend" / "candidate" / "index.html"
    ).read_text()
    notice = page[page.index("<h2>Before you begin</h2>") : page.index("I understand and agree")]

    # Recorded, and it says so.
    assert "recorded" in notice
    assert "video and sound" in notice
    # A person may watch it.
    assert "watch the recording" in notice
    # And for how long it is kept, from the constant rather than typed.
    assert "{{RETENTION_DAYS}} days" in notice
    # The claim Phase 1 made, which this change makes false, is gone.
    assert "camera picture is not saved" not in notice


def test_the_notice_still_promises_no_analysis_of_the_video():
    """A recording a person watches is a different thing from a recording a
    system reads, and the candidate is told which this is."""
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2] / "frontend" / "candidate" / "index.html"
    ).read_text()
    notice = page[page.index("<h2>Before you begin</h2>") : page.index("I understand and agree")]

    assert "not scored" in notice
    assert "face" in notice and "expression" in notice


def test_nothing_feeds_the_video_to_a_model():
    """The boundary is deliberate, so it is worth a test that would notice it
    moving.

    Reads the imports rather than the text of the file. The first version of this
    grepped for words and failed on the module's own docstring saying it does not
    measure attention, which is the sort of check that looks strict and is not.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "recordings.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for forbidden in ("openai", "anthropic", "cv2", "numpy", "PIL", "torch", "feedback"):
        assert forbidden not in imported, (
            f"{forbidden!r} is imported by the recording module; the video is for "
            f"a person to watch and nothing else"
        )


def test_the_sweep_can_be_switched_off():
    """Only so tests can drive it by hand. It is on by default, which is the
    behaviour that matters: retention that has to be enabled is not retention."""
    from app import recordings

    assert recordings.sweep_enabled() is (not os.environ.get("RECORDING_SWEEP_DISABLED"))
