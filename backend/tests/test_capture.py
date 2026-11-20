"""Phase 3 wave 1 (D-58): the candidate's own audio to a WAV, and the CaptureManifest.

Frames are Pipecat's own UserAudioRawFrame in FramePushed, from a real
BaseInputTransport subclass as the source (as Daily's input transport pushes them,
D-33). The files are read back by Session D's reader (aftercall.store), the code that
consumes them, so a shape it cannot read fails here.
"""

import asyncio
import json
import math
import struct
import time
import wave

import pytest
from pipecat.frames.frames import TextFrame, UserAudioRawFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_transport import TransportParams

from bot import capture as capture_module
from bot.capture import CandidateAudioCapture, capture_enabled, mitra_speaking_from

IID = "int_0123456789ab"
CHUNK = 320  # 20 ms at 16 kHz


class Clock:
    def __init__(self):
        self.t = 10.0

    def __call__(self):
        return self.t


def tone(n, freq=440.0, rate=16000, amp=8000, channels=1):
    out = []
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * freq * i / rate))
        out.extend([v] * channels)
    return struct.pack(f"<{len(out)}h", *out)


@pytest.fixture
def transport():
    return BaseInputTransport(TransportParams())


def push(cap, transport, pid, pcm, *, rate=16000, channels=1, source=None):
    frame = UserAudioRawFrame(user_id=pid, audio=pcm, sample_rate=rate, num_channels=channels)
    return cap.on_push_frame(FramePushed(source=source or transport, destination=None, frame=frame,
                                         direction=None, timestamp=0))


def make(tmp_path, clock, candidates=("cand-1",), **kw):
    return CandidateAudioCapture(interview_id=IID, root=tmp_path, is_candidate=lambda pid: pid in candidates,
                                 clock=clock, **kw)


def read_wav(path):
    with wave.open(str(path)) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())


async def test_only_the_candidates_frames_from_the_transport_are_written(tmp_path, transport):
    clock = Clock()
    cap = make(tmp_path, clock)
    mine, watcher = tone(CHUNK, 440), tone(CHUNK, 1000)
    other_hop = FrameProcessor()  # the same frame seen again further down the pipeline
    for n in range(50):
        clock.t += 0.02
        await push(cap, transport, "cand-1", mine)
        await push(cap, transport, "watcher-1", watcher)
        await push(cap, transport, "cand-1", mine, source=other_hop)
        await cap.on_push_frame(FramePushed(source=transport, destination=None, frame=TextFrame("x"),
                                            direction=None, timestamp=0))
    cap.close()
    rate, channels, width, data = read_wav(cap.wav_path)
    assert (rate, channels, width) == (16000, 1, 2)
    assert data == mine * 50, "the candidate's audio, once each, nothing else"
    assert cap.stats["chunks_written"] == 50


async def test_the_manifest_is_written_last_atomically_and_reads_back_through_d_reader(tmp_path, transport,
                                                                                      monkeypatch):
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    from aftercall import store

    clock = Clock()
    cap = make(tmp_path, clock)
    for _ in range(100):
        clock.t += 0.02
        await push(cap, transport, "cand-1", tone(CHUNK))
    assert not cap.manifest_path.exists(), "no manifest while the call runs"
    cap.close()
    cap.write_manifest([(1.0, 3.5), (12.25, 15.0)])
    manifest = store.read_manifest(IID)
    audio = manifest.candidate_audio
    assert audio.relative_path == f"{IID}/candidate.wav" and audio.sample_rate_hz == 16000
    assert audio.channels == 1 and audio.sample_format == "s16le"
    assert audio.start_seconds == pytest.approx(10.0, abs=0.001), "the interview clock at the first sample"
    assert audio.duration_seconds == pytest.approx(2.0, abs=0.001)
    assert audio.bytes == cap.wav_path.stat().st_size
    assert store.audio_file(manifest) == cap.wav_path.resolve()
    assert [(r.start_seconds, r.end_seconds) for r in manifest.mitra_speaking] == [(1.0, 3.5), (12.25, 15.0)]
    assert sorted(p.name for p in cap.manifest_path.parent.iterdir()) == ["candidate.wav", "manifest.json"], \
        "no temporary file left behind"


async def test_a_dropped_call_is_filled_with_silence_so_the_file_stays_on_the_interview_clock(tmp_path, transport):
    clock = Clock()
    cap = make(tmp_path, clock)
    for _ in range(50):           # 10.02 .. 11.00
        clock.t += 0.02
        await push(cap, transport, "cand-1", tone(CHUNK))
    clock.t += 4.0                # away 4 s
    for _ in range(50):
        clock.t += 0.02
        await push(cap, transport, "cand-1", tone(CHUNK, 1000))
    cap.close()
    summary = cap.summary()
    assert summary["gap_fill_seconds"] == pytest.approx(4.0, abs=0.03)
    _, _, _, data = read_wav(cap.wav_path)
    # The second burst's first sample sits at its interview time minus the start.
    second_start = (11.0 + 4.0 + 0.02 - 0.02) - cap._first_at
    index = int(round(second_start * 16000)) * 2
    assert data[index:index + CHUNK * 2] == tone(CHUNK, 1000)[:CHUNK * 2]
    assert data[1 * 16000 * 2 + 400: 1 * 16000 * 2 + 800] == b"\x00" * 400


async def test_jitter_under_half_a_second_is_never_padded(tmp_path, transport):
    clock = Clock()
    cap = make(tmp_path, clock)
    for n in range(100):
        clock.t += 0.02 + (0.3 if n == 40 else 0.0)  # one late chunk, then a burst
        await push(cap, transport, "cand-1", tone(CHUNK))
    cap.close()
    assert cap.summary()["gap_fill_seconds"] == 0.0
    assert cap.summary()["written_seconds"] == pytest.approx(2.0, abs=0.001)


async def test_a_second_tab_is_not_mixed_in_and_a_reload_switches(tmp_path, transport):
    clock = Clock()
    cap = make(tmp_path, clock, candidates=("tab-1", "tab-2"), now=clock)
    a, b = tone(CHUNK, 440), tone(CHUNK, 1000)
    for _ in range(20):
        clock.t += 0.02
        await push(cap, transport, "tab-1", a)
        await push(cap, transport, "tab-2", b)  # both sending: tab-2 is not mixed in
    clock.t += 2.0                               # tab-1 gone (a reload): tab-2 takes over
    for _ in range(20):
        clock.t += 0.02
        await push(cap, transport, "tab-2", b)
    cap.close()
    assert cap.stats["chunks_other_connection"] == 20 and cap.stats["connections"] == ["tab-1", "tab-2"]
    _, _, _, data = read_wav(cap.wav_path)
    assert data.startswith(a * 20) and data.endswith(b * 20)


async def test_48k_stereo_is_written_as_16k_mono(tmp_path, transport):
    clock = Clock()
    cap = make(tmp_path, clock)
    for _ in range(50):
        clock.t += 0.02
        await push(cap, transport, "cand-1", tone(960, rate=48000, channels=2), rate=48000, channels=2)
    cap.close()
    rate, channels, _, data = read_wav(cap.wav_path)
    assert (rate, channels) == (16000, 1)
    assert len(data) // 2 == pytest.approx(16000, abs=20)


async def test_the_tap_never_waits_when_the_writer_is_stuck(tmp_path, transport, monkeypatch):
    """The writer thread blocked on the disk: the queue fills, chunks are dropped and
    counted, and every push still returns at once."""
    import threading

    stuck = threading.Event()
    real = wave.Wave_write.writeframesraw

    def slow(self, data):
        stuck.wait(5)
        return real(self, data)

    monkeypatch.setattr(wave.Wave_write, "writeframesraw", slow)
    clock = Clock()
    cap = make(tmp_path, clock, queue_chunks=20)
    slowest = 0.0
    for _ in range(200):
        clock.t += 0.02
        t0 = time.perf_counter()
        await push(cap, transport, "cand-1", tone(CHUNK))
        slowest = max(slowest, time.perf_counter() - t0)
    assert cap.stats["chunks_dropped_full"] >= 150
    assert slowest < 0.005, f"a push took {slowest * 1000:.1f} ms"
    stuck.set()
    cap.close()
    assert cap.stats["chunks_queued"] + cap.stats["chunks_dropped_full"] == 200


async def test_a_disk_failure_leaves_no_half_record_and_never_raises_into_the_call(tmp_path, transport, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")

    clock = Clock()
    cap = make(tmp_path, clock)
    monkeypatch.setattr(wave.Wave_write, "writeframesraw", broken)
    for _ in range(30):
        clock.t += 0.02
        await push(cap, transport, "cand-1", tone(CHUNK))
    cap.close()
    cap.write_manifest([(1.0, 2.0)])
    manifest = json.loads(cap.manifest_path.read_text())
    assert manifest["candidate_audio"] is None and manifest["mitra_speaking"] == [
        {"start_seconds": 1.0, "end_seconds": 2.0}]
    assert not cap.wav_path.exists() and "disk full" in cap.summary()["error"]


async def test_nobody_spoke_gives_a_manifest_with_no_audio(tmp_path):
    cap = make(tmp_path, Clock())
    cap.close()
    cap.write_manifest([])
    assert json.loads(cap.manifest_path.read_text())["candidate_audio"] is None


def test_the_switch(monkeypatch):
    for value, on in ((None, True), ("", True), ("1", True), ("0", False), ("false", False), ("OFF", False)):
        if value is None:
            monkeypatch.delenv(capture_module.ENABLED_ENV, raising=False)
        else:
            monkeypatch.setenv(capture_module.ENABLED_ENV, value)
        assert capture_enabled() is on


def test_mitras_end_time_is_always_recorded_even_when_playback_end_was_never_reported():
    """D-58.3: a reply whose playback was never reported ended (the call ended while it
    played) used to have audio_end_seconds None; now start + its audio length."""
    from bot.persistence import build_transcript
    from bot.realtime_timeline import RealtimeTimeline

    tl = RealtimeTimeline()
    tl.assistant_added("item_a", None)
    tl.assistant_text("item_a", "Hello, how are you?", 101.0)
    tl.assistant_audio("item_a", 48000 * 2, 100.5)          # 2 s of 24 kHz 16-bit audio
    tl.playback_started(100.6)
    tl.playback_stopped(102.6)
    tl.assistant_added("item_b", "item_a")
    tl.assistant_text("item_b", "Thank you. Goodbye.", 110.2)
    tl.assistant_audio("item_b", 24000 * 2, 110.0)          # 1 s; playback end never reported
    t = build_transcript(interview_id=IID, turns=[], duration_seconds=120.0, timeline=tl, clock_zero=100.0)
    mitra = [x for x in t.turns if x.speaker.value == "interviewer"]
    assert [(x.audio_start_seconds, x.audio_end_seconds, x.timing_source) for x in mitra] == [
        (0.6, 2.6, "playback"), (10.0, 11.0, "arrival")]
    assert mitra_speaking_from(t) == [(0.6, 2.6), (10.0, 11.0)]


async def test_the_retention_sweep_deletes_the_capture_after_ten_days_and_not_before(tmp_path, transport,
                                                                                      monkeypatch):
    """The files the bot writes are covered by Session C's sweep (app.recordings
    expire_aftercall_files, AFTERCALL_FOLDERS): kept inside the window, deleted after."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from app import db
    from app.db import Candidate, Interview, InterviewStatus, Job
    from app.recordings import expire_aftercall_files
    from shared.contracts import RECORDING_RETENTION_DAYS

    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sweep.db")
    await db.reset_engine()
    await db.create_all()
    started = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    async with db.get_sessionmaker()() as s:
        s.add(Job(id="job-1", title="PM", jd_text="jd"))
        s.add(Candidate(id="cand-1", job_id="job-1", name="Asha", cv_text="cv"))
        s.add(Interview(id=IID, candidate_id="cand-1", meeting_id=f"{uuid.uuid4().int % 10**9:09d}",
                        password="abcdefhj", daily_room_name="r", daily_room_url="https://x.daily.co/r",
                        room_expires_at=started + timedelta(hours=1), status=InterviewStatus.COMPLETED,
                        started_at=started))
        await s.commit()
    try:
        clock = Clock()
        cap = make(tmp_path, clock)
        for _ in range(10):
            clock.t += 0.02
            await push(cap, transport, "cand-1", tone(CHUNK))
        cap.close()
        cap.write_manifest([])
        assert cap.wav_path.exists() and cap.manifest_path.exists()
        inside = started + timedelta(days=RECORDING_RETENTION_DAYS) - timedelta(minutes=1)
        assert await expire_aftercall_files(inside) == 0 and cap.wav_path.exists()
        after = started + timedelta(days=RECORDING_RETENTION_DAYS, minutes=1)
        assert await expire_aftercall_files(after) == 1
        assert not cap.wav_path.parent.exists()
    finally:
        await db.reset_engine()
