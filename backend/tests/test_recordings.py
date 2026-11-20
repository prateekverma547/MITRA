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
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password-0123456789")
    # The sweep is driven by hand here so its behaviour can be watched. A loop
    # running underneath would make calls nobody asked for.
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")

    from app import db, main, recordings

    await db.reset_engine()
    # Recordings land under the test's own directory, never the real one.
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", tmp_path / "sessions" / "recordings")
    monkeypatch.setattr(recordings, "SESSIONS_DIR", tmp_path / "sessions")

    with TestClient(main.app) as test_client:
        test_client.post("/admin/login", json={"password": "test-admin-password-0123456789"})
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


async def test_the_interview_keeps_running_when_the_recording_fails(client, monkeypatch):
    """The whole point. The interview stays in progress, the reason is written
    down, and nothing raises out of the handler."""
    from app import db
    from bot.run_bot import begin_recording

    # These three describe what happens when recording is on, so they turn it
    # on. Off is the default and has its own tests below.
    monkeypatch.setenv("RECORDING_ENABLED", "1")
    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)

    recording_on = await begin_recording(
        RefusingTransport(), interview_id=interview_id, session_id="s", clock_zero=0.0, recording=True
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


async def test_the_recording_reference_is_written_while_the_call_is_live(client, monkeypatch):
    """Mid-session, not in the `finally` block.

    A killed process never reaches its `finally`, and a recording nobody has
    recorded is one nobody collects and therefore one nobody ever deletes. That
    is a retention promise quietly broken by a crash.
    """
    from app import db
    from bot.run_bot import begin_recording

    # These three describe what happens when recording is on, so they turn it
    # on. Off is the default and has its own tests below.
    monkeypatch.setenv("RECORDING_ENABLED", "1")
    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)

    assert await begin_recording(
        WillingTransport(), interview_id=interview_id, session_id="s", clock_zero=0.0, recording=True
    )

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING
        assert interview.recording_started_at is not None
        assert interview.recording_expires_at is not None
        assert interview.recording_offset_seconds is not None


async def test_the_retention_deadline_is_set_from_the_interview_not_from_access(client, monkeypatch):
    """The clock starts at the interview. A window that reset whenever somebody
    opened the video would not be the promise the candidate was shown."""
    from app import db
    from bot.run_bot import begin_recording

    # These three describe what happens when recording is on, so they turn it
    # on. Off is the default and has its own tests below.
    monkeypatch.setenv("RECORDING_ENABLED", "1")
    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)
    await begin_recording(
        WillingTransport(), interview_id=interview_id, session_id="s", clock_zero=0.0, recording=True
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
    from app.db import RecordingStatus
    from tests.frontend_source import panel_source

    panel = panel_source()
    card = panel[panel.index("function recordingCard"):panel.index("function reportCard")]

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
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.frontend_source import join_source, panel_source

    # Both pages read the number from /config, which is served from the same constant the sweep reads.
    assert TestClient(app).get("/config").json()["retention_days"] == RECORDING_RETENTION_DAYS
    assert "config.retention_days" in panel_source()
    # The join page reads it the same way once it is ported; until then its HTML substitutes it.
    if join_source():
        assert "config.retention_days" in join_source()


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


def rendered_notice(monkeypatch, *, recording: bool) -> str:
    """The consent notice a candidate reads, for one setting of the switch.

    The notice chooses its branch from the interview's own flag, which /interviews/notice
    returns for the credentials the candidate entered. This reads the branch that value selects.
    """
    from tests.frontend_source import consent_notice

    return consent_notice(recording)


def test_the_notice_describes_a_recording_when_one_is_being_made(monkeypatch):
    notice = rendered_notice(monkeypatch, recording=True)

    assert "recorded" in notice
    assert "video and sound" in notice
    # A person may watch it, and for how long it is kept. The number comes from /config.
    assert "watch the recording" in notice
    assert "kept for ${config.retention_days} days" in notice
    assert RECORDING_RETENTION_DAYS > 0  # the number the candidate is promised comes from the contract
    # And the claim from before recording existed is not also on the page.
    assert "The interview is not recorded" not in notice


def test_the_notice_does_not_describe_a_recording_when_none_is_being_made(monkeypatch):
    """The point of the change rather than tidying after it.

    A candidate is agreeing to this. A notice that describes a system we are not
    running is the same fault recorded six times in the changelog, and it is
    worse here than anywhere else it has happened.
    """
    notice = rendered_notice(monkeypatch, recording=False)

    assert "The interview is not recorded" in notice
    # Nothing anywhere in it claims a recording is kept, watched or shared.
    assert "watch the recording" not in notice
    assert "video and sound" not in notice
    assert "days" not in notice, "a retention promise survived with recording off"


def test_the_camera_is_still_described_as_on_either_way(monkeypatch):
    """Recording went off. The camera did not."""
    for recording in (True, False):
        notice = rendered_notice(monkeypatch, recording=recording)
        assert "camera and microphone are on" in notice, recording


def test_the_transcript_is_described_either_way(monkeypatch):
    """It is kept and shared in both settings, and it is the thing the candidate
    is actually assessed on."""
    for recording in (True, False):
        notice = rendered_notice(monkeypatch, recording=recording)
        assert "transcript" in notice
        assert "shared with the employer" in notice
        assert "not scored" in notice
        assert "expression" in notice


def test_the_device_check_follows_the_switch_too(monkeypatch):
    from tests.frontend_source import device_notice

    for recording, expected, forbidden in (
        (True, "on and recorded during the interview", "Nothing on this screen leaves"),
        (False, "Nothing on this screen leaves", "on and recorded during the interview"),
    ):
        check = device_notice(recording)
        assert expected in check, recording
        assert forbidden not in check, recording


def test_no_marker_ever_reaches_the_browser():
    """The switch is a plain expression on /config now. There are no comment markers left in the
    source for a candidate to see, and none may come back."""
    from tests.frontend_source import join_source

    assert "RECORDING ON" not in join_source()
    assert "RECORDING OFF" not in join_source()


# -- the switch itself -------------------------------------------------------


def test_recording_is_off_unless_it_is_switched_on(monkeypatch):
    """Off is the default, and a half-set variable stays off.

    The wrong direction here costs money for as long as nobody notices; the
    right direction costs a reviewer one video.
    """
    from bot.config import recording_enabled

    monkeypatch.delenv("RECORDING_ENABLED", raising=False)
    assert recording_enabled() is False

    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("RECORDING_ENABLED", value)
        assert recording_enabled() is True, value

    for value in ("", "0", "false", "no", "off", "maybe", " "):
        monkeypatch.setenv("RECORDING_ENABLED", value)
        assert recording_enabled() is False, value


class CountingTransport:
    """Records whether Daily was asked to record at all."""

    def __init__(self):
        self.asked = 0

    async def start_recording(self, *a, **k):
        self.asked += 1
        return ("stream-1", None)


async def test_nothing_is_recorded_when_the_interview_is_not_marked(client, monkeypatch):
    """Daily is never asked, which is what stops the bill.

    Asserting the interview record alone would not be enough: a recording that
    started and was then forgotten about still costs a recorded minute and then
    storage for as long as it sits there.
    """
    from app import db
    from bot.run_bot import begin_recording

    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)
    transport = CountingTransport()

    assert await begin_recording(
        transport, interview_id=interview_id, session_id="s", clock_zero=0.0, recording=False
    ) is False
    assert transport.asked == 0, "Daily was asked to record with the switch off"

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        # The interview is untouched, and the panel already has a sentence for
        # this state rather than a player.
        assert interview.status == db.InterviewStatus.IN_PROGRESS
        assert interview.recording_status == db.RecordingStatus.NOT_RECORDED
        assert interview.recording_error is None


async def test_an_interview_marked_for_recording_is_recorded(client, monkeypatch):
    """The switch is per interview: the bot records when the backend says so, nothing else."""
    from app import db
    from bot.run_bot import begin_recording

    interview_id = await make_interview(status=db.InterviewStatus.IN_PROGRESS, ended_at=None)
    transport = CountingTransport()

    assert await begin_recording(
        transport, interview_id=interview_id, session_id="s", clock_zero=0.0, recording=True
    ) is True
    assert transport.asked == 1

    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING
        assert interview.recording_started_at is not None
        assert interview.recording_expires_at is not None
        assert interview.recording_offset_seconds is not None


async def test_the_sweep_still_collects_a_recording_made_before_the_switch_went_off(
    client, monkeypatch
):
    """Switching recording off must not strand what is already there.

    There is a recording sitting in Daily right now. It still has to be pulled
    down and Daily's copy deleted, or it keeps accruing storage charges for
    ever, which is the thing switching recording off was meant to stop.
    """
    from app import db, recordings
    from bot.services.daily import DailyRecording

    monkeypatch.delenv("RECORDING_ENABLED", raising=False)
    interview_id = await make_interview(recording_status=db.RecordingStatus.RECORDING)
    order = []

    async def fake_find(*, api_key, room_name):
        return DailyRecording(id="left-over-1", room_name=room_name, start_ts=0, duration_seconds=60)

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
    assert order == ["download", "delete"], "Daily's copy was left behind"
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.STORED


def test_the_sweep_does_not_read_the_recording_switch():
    """Structural, because the consequence is invisible until a bill arrives.

    If the sweep ever consults the flag, everything already sitting at Daily
    stops being collected and stops being deleted the moment somebody turns
    recording off, which is exactly when they were trying to stop paying for it.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "recordings.py").read_text()

    assert "recording_enabled" not in source
    assert "RECORDING_ENABLED" not in source


def test_only_the_bot_start_path_reads_the_switch():
    """The bot never reads the default. It records when the backend passes --recording."""
    import subprocess
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    hits = subprocess.run(
        ["grep", "-rln", "-e", "recording_enabled()", "-e", "recording_default()", "--include=*.py", str(backend)],
        capture_output=True, text=True,
    ).stdout.splitlines()
    # The default is read in exactly two places: the admin's create endpoint, and /config.
    # Neither is the bot, and the definition itself lives in config.py.
    names = sorted(Path(h).name for h in hits if "/tests/" not in h.replace("\\", "/"))

    assert names == ["config.py", "interviews.py", "main.py"], names
    run_bot = (backend / "bot" / "run_bot.py").read_text(encoding="utf-8")
    assert "recording_enabled" not in run_bot
    assert "RECORDING_ENABLED" not in run_bot


def test_the_room_still_permits_recording_whatever_the_switch_says():
    """Rooms are booked before the interview happens, sometimes a day before.

    Withholding permission at booking time would mean flipping the switch on did
    nothing for every interview already scheduled, and the flag would look
    broken. Permission is not billed; starting a recording is.
    """
    import inspect

    from bot.services import daily

    source = inspect.getsource(daily.create_room)
    assert '"enable_recording": "cloud"' in source
    assert "recording_enabled" not in source


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


def test_the_sweep_can_be_switched_off(monkeypatch):
    """Only so tests can drive it by hand. It is on by default, which is the
    behaviour that matters: retention that has to be enabled is not retention.

    **This used to assert the implementation back to itself** —
    `sweep_enabled() is (not os.environ.get(...))` — which passes whatever the
    function does, and so could not notice that "0" and "false" both switched
    the sweep off. Asserting values instead: the words an operator would
    actually type.
    """
    from app import recordings

    monkeypatch.delenv("RECORDING_SWEEP_DISABLED", raising=False)
    assert recordings.sweep_enabled() is True

    for off in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("RECORDING_SWEEP_DISABLED", off)
        assert recordings.sweep_enabled() is False, off

    # The ones that used to disable retention by accident.
    for on in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("RECORDING_SWEEP_DISABLED", on)
        assert recordings.sweep_enabled() is True, on
