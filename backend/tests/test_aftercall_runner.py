"""The after-call runner (Session D): the switch, once-only, retries, statuses,
pause, the lock, finding work, retention, and no words in its logs.

The check itself is replaced by a counting stand-in here (the real one is in
test_aftercall_second_voice.py); the rest is the real code on real files.
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from loguru import logger

from aftercall import runner, settings, store
from aftercall import second_voice as sv
from shared.interview_contracts import (
    AfterCallState,
    AfterCallStatus,
    CandidateAudioCapture,
    CaptureManifest,
    SecondVoiceSignal,
    TimeRange,
)

IID = "int_ac1"
CANARY = "zanzibar-quokka-1197"  # stands in for a candidate's words


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv(settings.ENABLED_ENV, "1")
    monkeypatch.delenv(settings.PAUSED_ENV, raising=False)
    return tmp_path / "recordings"


def write_manifest(iid=IID, audio=True, other_id=None):
    d = store.capture_dir(iid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.wav").write_bytes(b"RIFF")
    m = CaptureManifest(interview_id=other_id or iid, candidate_audio=CandidateAudioCapture(
        relative_path=f"{iid}/candidate.wav", start_seconds=2.0, duration_seconds=60.0) if audio else None)
    (d / "manifest.json").write_text(m.model_dump_json())


class FakeCheck:
    """Counts calls; returns a signal, None, or raises what it is told to."""

    def __init__(self, outcome="signal"):
        self.calls = 0
        self.outcome = outcome

    def __call__(self, interview_id, manifest, mitra_turns, should_stop):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if self.outcome == "none":
            return None, {}
        return SecondVoiceSignal(
            interview_id=interview_id, ranges=[TimeRange(start_seconds=12.0, end_seconds=15.0)], confidence=0.4,
            method="stand-in", method_version="0", wording=sv.wording([(12.0, 15.0)]),
            integrity_event_id=f"aftercall-second-voice-{interview_id}"), {"ranges": 1}


def states(status):
    return {c.check.value: c.state.value for c in status.checks}


# -- the switch -------------------------------------------------------------------------------------


def test_switched_off_it_does_nothing_at_all(root, monkeypatch):
    monkeypatch.delenv(settings.ENABLED_ENV)
    write_manifest()
    fake = FakeCheck()
    monkeypatch.setattr(runner, "second_voice_check", fake)
    assert runner.run_job(IID, mitra_turns=[]) is None
    assert fake.calls == 0
    assert not (root / "aftercall").exists(), "nothing may be written while switched off"


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "False "])
def test_the_switch_is_off_unless_clearly_on(monkeypatch, value):
    monkeypatch.setenv(settings.ENABLED_ENV, value)
    assert settings.enabled() is False


def test_switched_off_the_background_pass_runs_nothing(root, monkeypatch):
    monkeypatch.delenv(settings.ENABLED_ENV)

    async def boom():
        raise AssertionError("find_work must not even be asked")

    monkeypatch.setattr(runner, "find_work", boom)
    import asyncio

    assert asyncio.run(runner.run_pass()) == 0


# -- once only, statuses, retries ------------------------------------------------------------------------


def test_a_second_run_does_no_work_and_changes_nothing(root, monkeypatch):
    write_manifest()
    fake = FakeCheck()
    monkeypatch.setattr(runner, "second_voice_check", fake)
    first = runner.run_job(IID, mitra_turns=[])
    files = {p: p.read_bytes() for p in store.result_dir(IID).iterdir() if p.is_file()}
    second = runner.run_job(IID, mitra_turns=[])
    assert fake.calls == 1, "a re-run must not run the check again"
    assert states(first) == states(second) == {"second_voice": "done", "screen_content": "skipped"}
    assert {p: p.read_bytes() for p in store.result_dir(IID).iterdir() if p.is_file()} == files
    assert runner.needs_work(second, store.read_attempts(IID)) is False


def test_statuses_and_results_match_the_contracts(root, monkeypatch):
    write_manifest()
    monkeypatch.setattr(runner, "second_voice_check", FakeCheck())
    runner.run_job(IID, mitra_turns=[])
    status = AfterCallStatus.model_validate_json(store.status_path(IID).read_text())
    sv_check = next(c for c in status.checks if c.check.value == "second_voice")
    screen = next(c for c in status.checks if c.check.value == "screen_content")
    assert sv_check.started_at and sv_check.finished_at and sv_check.reason is None
    assert screen.state == AfterCallState.SKIPPED and "wave 2" in screen.reason
    signal = store.read_signal(IID)
    events = store.read_events(IID)
    assert signal and events and events[0].event_id == signal.integrity_event_id
    assert events[0].source.value == "after_call" and events[0].reminder.value == "recorded_only"


def test_nothing_found_writes_no_signal(root, monkeypatch):
    write_manifest()
    monkeypatch.setattr(runner, "second_voice_check", FakeCheck("none"))
    status = runner.run_job(IID, mitra_turns=[])
    assert states(status)["second_voice"] == "done"
    assert store.read_signal(IID) is None and store.read_events(IID) == []


def test_a_failing_check_is_retried_up_to_the_limit_then_left_failed(root, monkeypatch):
    write_manifest()
    fake = FakeCheck(RuntimeError("model crashed"))
    monkeypatch.setattr(runner, "second_voice_check", fake)
    for n in range(1, settings.MAX_ATTEMPTS + 1):
        status = runner.run_job(IID, mitra_turns=[])
        check = next(c for c in status.checks if c.check.value == "second_voice")
        assert check.state == AfterCallState.FAILED
        assert f"attempt {n} of {settings.MAX_ATTEMPTS}" in check.reason
    runner.run_job(IID, mitra_turns=[])
    assert fake.calls == settings.MAX_ATTEMPTS
    assert runner.needs_work(store.read_status(IID), store.read_attempts(IID)) is False
    # one failure does not block the other check
    assert states(status)["screen_content"] == "skipped"


def test_a_failure_then_a_success_ends_done(root, monkeypatch):
    write_manifest()
    fake = FakeCheck(RuntimeError("transient"))
    monkeypatch.setattr(runner, "second_voice_check", fake)
    runner.run_job(IID, mitra_turns=[])
    fake.outcome = "signal"
    status = runner.run_job(IID, mitra_turns=[])
    assert states(status)["second_voice"] == "done" and fake.calls == 2


def test_a_check_that_cannot_say_anything_is_skipped_with_its_reason(root, monkeypatch):
    write_manifest()
    monkeypatch.setattr(runner, "second_voice_check", FakeCheck(sv.CheckSkipped("too little speech")))
    status = runner.run_job(IID, mitra_turns=[])
    check = next(c for c in status.checks if c.check.value == "second_voice")
    assert check.state == AfterCallState.SKIPPED and check.reason == "too little speech"


def test_without_mitras_times_the_real_check_is_skipped_not_run(root):
    """Priority 1: Mitra's leaked voice must never be reported; without her
    speaking times it cannot be told apart, so the check does not guess."""
    write_manifest()
    status = runner.run_job(IID, mitra_turns=None)
    check = next(c for c in status.checks if c.check.value == "second_voice")
    assert check.state == AfterCallState.SKIPPED and "Mitra's speaking times" in check.reason


def test_no_audio_captured_is_skipped(root):
    write_manifest(audio=False)
    status = runner.run_job(IID, mitra_turns=[])
    check = next(c for c in status.checks if c.check.value == "second_voice")
    assert check.state == AfterCallState.SKIPPED and "no candidate audio" in check.reason


def test_a_manifest_for_another_interview_fails_once_and_for_all(root, monkeypatch):
    write_manifest(other_id="int_other")
    fake = FakeCheck()
    monkeypatch.setattr(runner, "second_voice_check", fake)
    status = runner.run_job(IID, mitra_turns=[])
    assert states(status)["second_voice"] == "failed" and fake.calls == 0
    assert runner.needs_work(store.read_status(IID), store.read_attempts(IID)) is False


def test_no_manifest_yet_means_not_ready(root):
    assert runner.run_job(IID, mitra_turns=[]) is None


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, "int 1"])
def test_ids_that_are_not_plain_identifiers_are_refused(root, bad):
    with pytest.raises(store.BadInput):
        runner.run_job(bad, mitra_turns=[])


def test_an_audio_path_cannot_leave_the_capture_folder(root, tmp_path):
    (root / "capture").mkdir(parents=True)
    os.symlink(tmp_path, root / "capture" / "escape")
    m = CaptureManifest(interview_id=IID, candidate_audio=CandidateAudioCapture(
        relative_path="escape/secret.wav", start_seconds=0, duration_seconds=1))
    with pytest.raises(store.BadInput):
        store.audio_file(m)


# -- pause and the lock -----------------------------------------------------------------------------------


def test_paused_before_a_check_leaves_it_pending(root, monkeypatch):
    write_manifest()
    fake = FakeCheck()
    monkeypatch.setattr(runner, "second_voice_check", fake)
    (root / "aftercall").mkdir(parents=True)
    (root / "aftercall" / "PAUSED").write_text("")
    assert settings.paused()
    status = runner.run_job(IID, mitra_turns=[])
    assert fake.calls == 0 and states(status)["second_voice"] == "pending"
    (root / "aftercall" / "PAUSED").unlink()
    status = runner.run_job(IID, mitra_turns=[])
    assert fake.calls == 1 and states(status)["second_voice"] == "done"


def test_paused_part_way_goes_back_to_pending_without_using_an_attempt(root, monkeypatch):
    write_manifest()
    monkeypatch.setattr(runner, "second_voice_check", FakeCheck(sv.Paused()))
    status = runner.run_job(IID, mitra_turns=[])
    assert states(status)["second_voice"] == "pending"
    assert store.read_attempts(IID).get("second_voice", 0) == 0


def test_the_paused_setting_pauses(monkeypatch, root):
    monkeypatch.setenv(settings.PAUSED_ENV, "1")
    assert settings.paused()


def test_a_second_runner_on_the_same_interview_does_nothing(root, monkeypatch):
    write_manifest()
    fake = FakeCheck()
    monkeypatch.setattr(runner, "second_voice_check", fake)
    held = store.Lock(IID)
    assert held.acquire()
    try:
        assert runner.run_job(IID, mitra_turns=[]) is None
        assert fake.calls == 0
    finally:
        held.release()
    assert runner.run_job(IID, mitra_turns=[]) is not None and fake.calls == 1


def test_a_lock_left_by_a_dead_process_is_taken_over(root, monkeypatch):
    write_manifest()
    monkeypatch.setattr(runner, "second_voice_check", FakeCheck())
    lock = store.result_dir(IID) / ".lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"999999 {time.time()}")  # no such process
    assert runner.run_job(IID, mitra_turns=[]) is not None


def test_a_check_left_running_by_a_crash_is_run_again(root, monkeypatch):
    write_manifest()
    fake = FakeCheck()
    monkeypatch.setattr(runner, "second_voice_check", fake)
    status = runner._set(runner._blank(IID), runner.AfterCallCheckName.SECOND_VOICE,
                         state=AfterCallState.RUNNING, started_at=datetime.now(UTC))
    store.write_status(status)
    store.write_attempts(IID, {"second_voice": 1})
    assert runner.needs_work(store.read_status(IID), store.read_attempts(IID))
    status = runner.run_job(IID, mitra_turns=[])
    assert states(status)["second_voice"] == "done" and fake.calls == 1


# -- finding work, Mitra's times and no words, from the database --------------------------------------------


@pytest.fixture
async def database(tmp_path, monkeypatch, root):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ac.db")
    from app import db

    await db.reset_engine()
    await db.create_all()
    async with db.get_sessionmaker()() as session:
        session.add(db.Job(id="job_ac", jd_text="jd", spec_status=db.SpecStatus.READY))
        await session.commit()
    async with db.get_sessionmaker()() as session:
        session.add(db.Candidate(id="cand_ac", job_id="job_ac", name="A Candidate", cv_text="cv",
                                 blueprint_status=db.BlueprintStatus.READY))
        await session.commit()
    yield db
    await db.reset_engine()


async def add_interview(db, iid, status, turns):
    async with db.get_sessionmaker()() as session:
        session.add(db.Interview(
            id=iid, candidate_id="cand_ac", meeting_id=f"m-{iid}", password="pw", daily_room_name=f"r-{iid}",
            daily_room_url="https://example.daily.co/r", room_expires_at=datetime.now(UTC) + timedelta(hours=1),
            status=status,
            transcript=None if turns is None else {"interview_id": iid, "turns": turns, "duration_seconds": 60.0}))
        await session.commit()


TURNS = [
    {"speaker": "interviewer", "text": "Tell me about your last project.", "at_seconds": 3.0,
     "audio_start_seconds": 0.5, "audio_end_seconds": 3.2},
    {"speaker": "candidate", "text": f"I built the {CANARY} service.", "at_seconds": 9.0,
     "audio_start_seconds": 4.0, "audio_end_seconds": 8.8},
    {"speaker": "interviewer", "text": "What went wrong?", "at_seconds": 11.0,
     "audio_start_seconds": 9.5, "audio_end_seconds": 11.1},
]


async def test_find_work_lists_completed_interviews_with_a_manifest_only(database):
    db = database
    await add_interview(db, "int_done", db.InterviewStatus.COMPLETED, TURNS)
    await add_interview(db, "int_live", db.InterviewStatus.IN_PROGRESS, TURNS)
    await add_interview(db, "int_nomanifest", db.InterviewStatus.COMPLETED, TURNS)
    write_manifest("int_done")
    write_manifest("int_live")
    assert await runner.find_work() == ["int_done"]
    status = runner._blank("int_done")
    for c in runner.CHECKS:
        status = runner._set(status, c, state=AfterCallState.SKIPPED, reason="x")
    store.write_status(status)
    assert await runner.find_work() == [], "a finished interview is not picked up again"


async def test_mitras_times_come_from_the_transcript_without_its_words(database):
    db = database
    await add_interview(db, "int_t", db.InterviewStatus.COMPLETED, TURNS)
    assert await runner.mitra_turns_from_db("int_t") == [(0.5, 3.2), (9.5, 11.1)]
    no_times = [dict(TURNS[0], audio_start_seconds=None)] + TURNS[1:]
    await add_interview(db, "int_u", db.InterviewStatus.COMPLETED, no_times)
    assert await runner.mitra_turns_from_db("int_u") is None


async def test_no_candidate_words_reach_a_log_line_or_a_result(database, monkeypatch):
    db = database
    await add_interview(db, IID, db.InterviewStatus.COMPLETED, TURNS)
    write_manifest()
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        turns = await runner.mitra_turns_from_db(IID)
        await runner.find_work()
        monkeypatch.setattr(runner, "second_voice_check", FakeCheck(RuntimeError(f"boom near {CANARY}")))
        runner.run_job(IID, mitra_turns=turns)
        monkeypatch.setattr(runner, "second_voice_check", FakeCheck())
        runner.run_job(IID, mitra_turns=turns)
    finally:
        logger.remove(sink)
    assert lines, "the run must log something, or this test proves nothing"
    texts = lines + [p.read_text() for p in store.result_dir(IID).iterdir() if p.is_file()]
    assert not [t for t in texts if CANARY in t], "a candidate's words reached a log line or a result file"
    assert not [t for t in texts if "Tell me about" in t], "Mitra's words reached a log line or a result file"


# -- retention: one rule, the recordings sweep's (C-122, C-137) -------------------------------------


def test_retention_is_the_recordings_sweeps_and_covers_exactly_our_folders(root):
    from app.recordings import AFTERCALL_FOLDERS

    assert set(AFTERCALL_FOLDERS) == {settings.capture_root().name, settings.aftercall_root().name}
    assert not hasattr(store, "purge_expired"), "a second retention rule would have a second clock"
    assert settings.RETENTION_DAYS == 10


# -- Mitra's times from the manifest (C-139) ------------------------------------------------------------


def test_mitras_times_in_the_manifest_win_and_the_transcript_is_the_fallback():
    with_times = CaptureManifest(interview_id=IID, mitra_speaking=[TimeRange(start_seconds=1, end_seconds=4)])
    without = CaptureManifest(interview_id=IID)
    assert runner.mitra_times(with_times, None) == [(1.0, 4.0)]
    assert runner.mitra_times(with_times, [(9.0, 9.5)]) == [(1.0, 4.0)]
    assert runner.mitra_times(without, [(9.0, 9.5)]) == [(9.0, 9.5)]
    assert runner.mitra_times(without, None) is None


async def test_the_transcript_is_not_read_when_the_manifest_has_mitras_times(root, monkeypatch):
    async def must_not_read(_iid):
        raise AssertionError("the transcript was read although the manifest has Mitra's times")

    monkeypatch.setattr(runner, "mitra_turns_from_db", must_not_read)
    d = store.capture_dir(IID)
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(CaptureManifest(
        interview_id=IID, mitra_speaking=[TimeRange(start_seconds=0, end_seconds=2)]).model_dump_json())
    assert await runner.job_mitra_turns(IID) is None
    (d / "manifest.json").write_text(CaptureManifest(interview_id=IID).model_dump_json())
    with pytest.raises(AssertionError, match="transcript was read"):
        await runner.job_mitra_turns(IID)


def test_with_mitras_times_in_the_manifest_the_check_runs_without_the_transcript(root, monkeypatch):
    seen = {}

    def spy(interview_id, manifest, mitra_turns, should_stop):
        seen["turns"] = runner.mitra_times(manifest, mitra_turns)
        return None, {}

    d = store.capture_dir(IID)
    d.mkdir(parents=True)
    (d / "candidate.wav").write_bytes(b"RIFF")
    (d / "manifest.json").write_text(CaptureManifest(
        interview_id=IID, mitra_speaking=[TimeRange(start_seconds=3, end_seconds=5)],
        candidate_audio=CandidateAudioCapture(relative_path=f"{IID}/candidate.wav", start_seconds=0,
                                              duration_seconds=1)).model_dump_json())
    monkeypatch.setattr(runner, "second_voice_check", spy)
    status = runner.run_job(IID, mitra_turns=None)
    assert states(status)["second_voice"] == "done" and seen["turns"] == [(3.0, 5.0)]


# -- the job's own process: low priority, measured -------------------------------------------------------------


async def test_the_job_subprocess_runs_at_lowest_priority_and_reports_its_cost(database, monkeypatch):
    db = database
    await add_interview(db, IID, db.InterviewStatus.COMPLETED, None)  # no transcript, no Mitra times: skipped
    write_manifest()
    cost = await runner.run_job_in_subprocess(IID)
    assert cost["exit_code"] == 0
    assert cost["nice"] == 19 or cost["nice"] >= 10
    assert cost["states"] == {"second_voice": "skipped", "screen_content": "skipped"}
    assert {"wall_s", "cpu_s", "peak_rss_mb"} <= set(cost)
    assert json.loads(store.status_path(IID).read_text())["interview_id"] == IID
