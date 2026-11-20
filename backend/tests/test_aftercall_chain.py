"""The whole after-call chain, end to end (Session D): an interview ends -> the runner the
app starts at start-up picks it up BY ITSELF -> the second-voice check runs on the capture,
with Mitra's times from the manifest -> results stored -> Session C's signed-in route and
the IntegrityReport carry them -> Session C's sweep deletes them after 10 days.

Real app (lifespan), real job subprocesses, real models, a test database. The capture is
CONSTRUCTED to the bot's format (A-80): LibriSpeech reader 1462 as the candidate, silence
while Mitra speaks, a quiet gap mid-interview; the planted copy is the same track with
reader 2902 speaking in that gap. (Reader 2428 is NOT used here: its voice scores 0.72-0.76
against Mitra's reference, so the voice backup leaves it out: a recorded limitation, D-68.)
Nothing here is a real candidate.
"""

import asyncio
import os
import signal
import time
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aftercall import models, settings, store, testset as ts
from shared.interview_contracts import CandidateAudioCapture, CaptureManifest, TimeRange

FIX = Path(__file__).resolve().parents[1] / "aftercall" / "fixtures"
SR = 16000
START = 2.5  # interview clock at the capture's first sample
#: Mitra speaking (file seconds); the planted voice sits in the quiet gap 21-25 s.
MITRA = [(0.0, 4.0), (36.0, 40.0), (60.0, 64.0)]
PLANT = (21.5, 24.5)

try:
    models.verified_path(models.SPEAKER)
    HAVE_MODELS = True
except models.ModelUnavailable:
    HAVE_MODELS = False
pytestmark = pytest.mark.skipif(not HAVE_MODELS, reason="speaker model not installed: python -m aftercall.models fetch")


def capture_track(planted: bool, repeat: int = 1) -> np.ndarray:
    """The candidate's track as the bot saves it: Mitra's turns silent, answers, one quiet gap."""
    cand = ts.load(FIX / "candidate_ls1462.wav")
    other = ts.load(FIX / "other_ls2902.wav")
    level = 0.08
    one = np.zeros(int(80 * SR), dtype=np.float32)
    for a, b, src in ((4.5, 20.5, 0.0), (25.5, 35.5, 14.0), (40.5, 59.5, 3.0), (64.5, 79.5, 9.0)):
        seg = cand[int(src * SR):int(src * SR) + int((b - a) * SR)]
        one[int(a * SR):int(a * SR) + len(seg)] = ts.at_level(seg, level)
    if planted:
        seg = ts.at_level(other[: int((PLANT[1] - PLANT[0]) * SR)], level)
        one[int(PLANT[0] * SR):int(PLANT[0] * SR) + len(seg)] += seg
    x = np.concatenate([one] * repeat)
    return x + np.random.default_rng(0).standard_normal(len(x)).astype(np.float32) * 1e-4


def write_capture(iid: str, x: np.ndarray | None, repeat: int = 1, wav_bytes: bytes | None = None) -> None:
    d = store.capture_dir(iid)
    d.mkdir(parents=True, exist_ok=True)
    if wav_bytes is not None:
        (d / "candidate.wav").write_bytes(wav_bytes)
    elif x is not None:
        ts.write(d / "candidate.wav", x)
    mitra = [TimeRange(start_seconds=a + START + 80 * k, end_seconds=b + START + 80 * k)
             for k in range(repeat) for a, b in MITRA]
    duration = (len(x) / SR) if x is not None else 10.0
    manifest = CaptureManifest(interview_id=iid, mitra_speaking=mitra, candidate_audio=CandidateAudioCapture(
        relative_path=f"{iid}/candidate.wav", start_seconds=START, duration_seconds=duration))
    tmp = d / ".manifest.tmp"
    tmp.write_text(manifest.model_dump_json())
    tmp.replace(d / "manifest.json")  # last and atomic, as the bot writes it (A-80)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/chain.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv(settings.ENABLED_ENV, "1")  # this test environment only
    monkeypatch.delenv(settings.PAUSED_ENV, raising=False)
    monkeypatch.setattr(settings, "LOOP_SECONDS", 0.5)  # the loop's pace (60 s in production)
    return tmp_path


async def seed(*ids, status="in_progress"):
    from app import db

    async with db.get_sessionmaker()() as s:
        if await s.get(db.Job, "job_chain") is None:
            s.add(db.Job(id="job_chain", jd_text="jd", spec_status=db.SpecStatus.READY))
            await s.commit()
    async with db.get_sessionmaker()() as s:
        if await s.get(db.Candidate, "cand_chain") is None:
            s.add(db.Candidate(id="cand_chain", job_id="job_chain", name="Constructed", cv_text="cv",
                               blueprint_status=db.BlueprintStatus.READY))
            await s.commit()
    async with db.get_sessionmaker()() as s:
        for iid in ids:
            s.add(db.Interview(id=iid, candidate_id="cand_chain", meeting_id=f"m-{iid}", password="chain-meeting-pw",
                               daily_room_name=f"r-{iid}", daily_room_url="https://example.daily.co/r",
                               room_expires_at=datetime.now(UTC) + timedelta(hours=1), status=status,
                               started_at=datetime.now(UTC) - timedelta(minutes=2), transcript=None))
        await s.commit()


async def end_interview(iid, started_at=None):
    """What the bot's save does at the end (status completed, ended): the ending event."""
    from app import db

    async with db.get_sessionmaker()() as s:
        row = await s.get(db.Interview, iid)
        row.status = db.InterviewStatus.COMPLETED
        row.ended_at = datetime.now(UTC)
        if started_at is not None:
            row.started_at = started_at
        await s.commit()


async def wait_for(pred, timeout=150.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what}")


def state(iid, check="second_voice"):
    s = store.read_status(iid)
    return None if s is None else next(c for c in s.checks if c.check.value == check).state.value


def client():
    from app import main

    return TestClient(main.app)


async def reset_db():
    from app import db

    await db.reset_engine()


# -- the chain ----------------------------------------------------------------------------------------


async def test_the_chain_from_an_ended_interview_to_the_report(env):
    await reset_db()
    clean, planted = "int_chain_clean", "int_chain_plant"
    with client() as c:
        assert c.post("/admin/login", json={"password": "test-admin-pw"}).status_code == 200
        await seed(clean, planted)
        write_capture(clean, capture_track(planted=False))
        write_capture(planted, capture_track(planted=True))
        # 1. still in progress: the runner leaves it alone
        await asyncio.sleep(2.0)
        assert store.read_status(clean) is None and store.read_status(planted) is None
        # 2. the interview ends; nothing is called: the start-up runner must find it
        await end_interview(clean)
        await end_interview(planted)
        await wait_for(lambda: state(clean) == "done" and state(planted) == "done", what="both checks done")
        # 3-4. stored with the right status; Mitra's times came from the manifest (transcript is None)
        for iid in (clean, planted):
            s = store.read_status(iid)
            assert {x.check.value: x.state.value for x in s.checks} == {"second_voice": "done", "screen_content": "skipped"}
        assert store.read_signal(clean) is None, "the clean capture was flagged"
        sig = store.read_signal(planted)
        truth = (PLANT[0] + START, PLANT[1] + START)
        assert sig and ts.overlaps([(r.start_seconds, r.end_seconds) for r in sig.ranges], [truth]) == 1
        # 5. Session C's signed-in route and the IntegrityReport
        r = c.get(f"/api/proctoring/interviews/{planted}/after-call")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True and body["status"]["checks"][0]["state"] == "done"
        assert ts.overlaps([(x["start_seconds"], x["end_seconds"]) for x in body["second_voice"]["ranges"]], [truth]) == 1
        assert "chain-meeting-pw" not in r.text and "test-admin-pw" not in r.text
        rc = c.get(f"/api/proctoring/interviews/{clean}/after-call").json()
        assert rc["second_voice"] is None and rc["status"]["checks"][0]["state"] == "done"
        rep = c.get(f"/api/proctoring/interviews/{planted}/report").json()
        ev = [e for e in rep["events"] if e["kind"] == "second_voice"]
        assert rep["analysed_after_call"] is True and len(ev) == 1
        assert ev[0]["source"] == "after_call" and ev[0]["reminder"] == "recorded_only"
        assert ev[0]["start_seconds"] < truth[1] and ev[0]["end_seconds"] > truth[0]
        assert rep["second_voice"]["integrity_event_id"] == ev[0]["event_id"]
        assert not [e for e in c.get(f"/api/proctoring/interviews/{clean}/report").json()["events"]
                    if e["kind"] == "second_voice"]
        assert c.get("/api/proctoring/interviews/int_nobody/after-call").status_code == 404
    await reset_db()


async def test_switched_off_nothing_runs_and_nothing_is_stored(env, monkeypatch):
    monkeypatch.delenv(settings.ENABLED_ENV)
    await reset_db()
    with client():
        await seed("int_chain_off")
        write_capture("int_chain_off", capture_track(planted=True))
        await end_interview("int_chain_off")
        await asyncio.sleep(3.0)  # six passes of the loop
    assert not (env / "recordings" / "aftercall").exists()
    await reset_db()


# -- robustness ------------------------------------------------------------------------------------------


def _job_pid(iid):
    lock = store.result_dir(iid) / ".lock"
    try:
        return int(lock.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


async def test_a_restart_mid_run_finishes_once_never_stuck_running(env):
    iid = "int_chain_restart"
    await reset_db()
    with client():
        await seed(iid)
        write_capture(iid, capture_track(planted=True, repeat=3), repeat=3)  # 4 minutes
        await end_interview(iid)
        await wait_for(lambda: state(iid) == "running" and _job_pid(iid), what="the job running")
        os.kill(_job_pid(iid), signal.SIGKILL)  # the container dies mid-run
    assert state(iid) == "running", "the crash must leave it running, or this test proves nothing"
    await reset_db()
    with client():  # the app starts again
        await wait_for(lambda: state(iid) == "done", what="done after the restart")
    assert store.read_attempts(iid)["second_voice"] == 2
    assert len(store.read_events(iid)) == 1 and store.read_signal(iid) is not None
    assert sorted(p.name for p in store.result_dir(iid).iterdir()) == [
        "attempts.json", "integrity_events.json", "second_voice.json", "status.json"]
    await reset_db()


async def test_pause_mid_run_stops_between_pieces_and_resumes(env):
    iid = "int_chain_pause"
    await reset_db()
    with client():
        await seed(iid)
        write_capture(iid, capture_track(planted=True, repeat=3), repeat=3)
        await end_interview(iid)
        await wait_for(lambda: state(iid) == "running", what="the job running")
        pause = settings.aftercall_root() / "PAUSED"
        pause.write_text("")  # what an operator does mid-run (the job reads it, D-67)
        await wait_for(lambda: state(iid) == "pending", timeout=60, what="paused back to pending")
        assert store.read_attempts(iid).get("second_voice", 0) == 0, "a pause is not an attempt"
        await asyncio.sleep(2.0)
        assert state(iid) == "pending", "nothing runs while paused"
        pause.unlink()
        await wait_for(lambda: state(iid) == "done", what="resumed and done")
    assert store.read_attempts(iid)["second_voice"] == 1
    await reset_db()


async def test_a_missing_or_broken_wav_fails_clearly_without_a_retry_storm(env):
    missing, broken, healthy = "int_chain_missing", "int_chain_broken", "int_chain_ok"
    await reset_db()
    with client():
        await seed(missing, broken, healthy)
        write_capture(missing, None)
        write_capture(broken, None, wav_bytes=b"RIFF\x00\x00\x00\x00WAVEgarbage" * 10)
        write_capture(healthy, capture_track(planted=False))
        for iid in (missing, broken, healthy):
            await end_interview(iid)
        await wait_for(lambda: all(store.read_attempts(i).get("second_voice") == settings.MAX_ATTEMPTS
                                   for i in (missing, broken)) and state(healthy) == "done",
                       what="the bad ones at their limit, the good one done")
        await asyncio.sleep(3.0)  # six more passes: no further attempts
        for iid, why in ((missing, "FileNotFoundError"), (broken, "Error")):
            s = store.read_status(iid)
            check = next(x for x in s.checks if x.check.value == "second_voice")
            assert check.state.value == "failed" and why in check.reason and "attempt 3 of 3" in check.reason
            assert store.read_attempts(iid)["second_voice"] == settings.MAX_ATTEMPTS
    await reset_db()


# -- retention: Session C's sweep, the only rule ------------------------------------------------------------


async def test_c_sweep_deletes_an_old_copy_and_keeps_the_fresh_one(env):
    from app.recordings import expire_aftercall_files

    old, fresh = "int_chain_old", "int_chain_fresh"
    await reset_db()
    with client():
        await seed(old, fresh)
        x = capture_track(planted=False)
        write_capture(old, x)
        write_capture(fresh, x)
        await end_interview(old, started_at=datetime.now(UTC) - timedelta(days=11))
        await end_interview(fresh)
        await wait_for(lambda: state(old) == "done" and state(fresh) == "done", what="both done")
        deleted = await expire_aftercall_files(datetime.now(UTC))
    assert deleted == 2  # capture/<old> and aftercall/<old>
    assert not store.capture_dir(old).exists() and not store.result_dir(old).exists()
    assert store.capture_dir(fresh).exists() and store.read_status(fresh) is not None
    await reset_db()
