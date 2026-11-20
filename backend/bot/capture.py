"""The candidate's own audio, saved for the after-call checks (phase 3 wave 1, D-58).

What it writes, under the recordings folder (app.recordings.recordings_root(), the
setting RECORDINGS_DIR), the layout Session D reads (aftercall/settings.py):
    capture/<interview_id>/candidate.wav   16 kHz, mono, 16-bit little-endian PCM
    capture/<interview_id>/manifest.json   a CaptureManifest, written LAST, atomically
                                           (a temporary file, then a rename), and only
                                           once the WAV is closed

Where the audio comes from (D-33): Pipecat's Daily transport captures each
participant's microphone separately and pushes it as UserAudioRawFrame with user_id =
the Daily participant id. Only frames from a candidate connection (presence knows
which) are kept, so the file holds the candidate alone (D-35): never Mitra, never a
watcher.

Off the reply path (D-37): the observer only does put_nowait into a bounded queue
(QUEUE_CHUNKS, 10 s of 20 ms chunks); when full the chunk is dropped and counted,
never waited for. A writer thread does the file work. Nothing here can delay a frame.

The file stays on the interview clock: `start_seconds` is the interview clock at the
first sample, and a gap longer than GAP_FILL_SECONDS (a dropped call, a reload) is
filled with silence, so a time t in the interview is at (t - start_seconds) in the
file, within GAP_FILL_SECONDS. A second candidate connection sending at the same time
(two tabs) is not mixed in: one connection at a time, switching when the current one
has been quiet for SWITCH_AFTER_SECONDS.

CANDIDATE_AUDIO_CAPTURE=0 (or false/no/off) turns it off; on by default.
"""

from __future__ import annotations

import audioop
import json
import os
import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import UserAudioRawFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.transports.base_input import BaseInputTransport

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
#: 10 s of 20 ms chunks (D-34's measured queue).
QUEUE_CHUNKS = 500
#: A gap longer than this between chunks is filled with silence.
GAP_FILL_SECONDS = 0.5
#: A second candidate connection is used only once the current one is this quiet.
SWITCH_AFTER_SECONDS = 1.0
ENABLED_ENV = "CANDIDATE_AUDIO_CAPTURE"
_OFF = {"0", "false", "no", "off"}


def capture_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() not in _OFF


def capture_dir(root: Path, interview_id: str) -> Path:
    return Path(root) / "capture" / interview_id


@dataclass
class _Chunk:
    pcm: bytes
    sample_rate: int
    channels: int
    #: Interview clock when the chunk reached the bot.
    at_seconds: float


_STOP = object()


@dataclass(eq=False)  # observers live in a set: keep the default hash (CLAUDE.md, M3)
class CandidateAudioCapture(BaseObserver):
    interview_id: str
    root: Path
    #: (participant id) -> bool: is this a candidate connection right now.
    is_candidate: object
    #: () -> interview seconds now (the transcript clock).
    clock: object
    queue_chunks: int = QUEUE_CHUNKS
    now: object = time.monotonic

    stats: dict = field(default_factory=lambda: {
        "chunks_queued": 0, "chunks_dropped_full": 0, "chunks_other_connection": 0,
        "chunks_written": 0, "gap_fill_seconds": 0.0, "resampled_chunks": 0, "connections": [],
        # The tap's own cost on the event loop, measured (the reply-path evidence).
        "tap_seconds_total": 0.0, "tap_seconds_max": 0.0, "queue_depth_max": 0})
    _queue: queue.Queue | None = None
    _thread: threading.Thread | None = None
    _current: str | None = None
    _current_last: float = 0.0
    _first_at: float | None = None
    _written_samples: int = 0
    _error: str | None = None
    _closed: bool = False
    _ratecv_state: object = None

    def __post_init__(self):
        super().__init__()
        self._queue = queue.Queue(maxsize=self.queue_chunks)
        self.wav_path = capture_dir(self.root, self.interview_id) / "candidate.wav"
        self.manifest_path = capture_dir(self.root, self.interview_id) / "manifest.json"

    # -- the tap (on the event loop: put_nowait only) -----------------------------------

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if not isinstance(frame, UserAudioRawFrame) or self._closed:
            return
        started = time.perf_counter()
        try:
            self._tap(data, frame)
        finally:
            spent = time.perf_counter() - started
            self.stats["tap_seconds_total"] += spent
            self.stats["tap_seconds_max"] = max(self.stats["tap_seconds_max"], spent)

    def _tap(self, data: FramePushed, frame: UserAudioRawFrame) -> None:
        # Every hop of a frame is observed; take it once, as the transport pushes it.
        if not isinstance(data.source, BaseInputTransport):
            return
        pid = frame.user_id
        if not pid or not self.is_candidate(pid):
            return
        mono = self.now()
        if pid != self._current:
            if self._current is not None and mono - self._current_last < SWITCH_AFTER_SECONDS:
                self.stats["chunks_other_connection"] += 1
                return
            self._current = pid
            self.stats["connections"].append(pid)
        self._current_last = mono
        if self._thread is None:
            self._thread = threading.Thread(target=self._write, name=f"capture-{self.interview_id}", daemon=True)
            self._thread.start()
        try:
            self._queue.put_nowait(_Chunk(bytes(frame.audio), frame.sample_rate, frame.num_channels,
                                          float(self.clock())))
            self.stats["chunks_queued"] += 1
            self.stats["queue_depth_max"] = max(self.stats["queue_depth_max"], self._queue.qsize())
        except queue.Full:
            self.stats["chunks_dropped_full"] += 1

    # -- the writer thread ------------------------------------------------------------------

    def _write(self) -> None:
        wav = None
        try:
            self.wav_path.parent.mkdir(parents=True, exist_ok=True)
            wav = wave.open(str(self.wav_path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            while True:
                chunk = self._queue.get()
                if chunk is _STOP:
                    break
                pcm = self._to_16k_mono(chunk)
                if self._first_at is None:
                    # The chunk's first sample: it arrived when its 20 ms had been heard.
                    self._first_at = max(0.0, chunk.at_seconds - len(pcm) / SAMPLE_WIDTH / SAMPLE_RATE)
                expected = int(round((chunk.at_seconds - self._first_at) * SAMPLE_RATE)) - len(pcm) // SAMPLE_WIDTH
                behind = expected - self._written_samples
                if behind > GAP_FILL_SECONDS * SAMPLE_RATE:
                    wav.writeframesraw(b"\x00\x00" * behind)
                    self._written_samples += behind
                    self.stats["gap_fill_seconds"] = round(self.stats["gap_fill_seconds"] + behind / SAMPLE_RATE, 3)
                wav.writeframesraw(pcm)
                self._written_samples += len(pcm) // SAMPLE_WIDTH
                self.stats["chunks_written"] += 1
        except Exception as exc:  # noqa: BLE001  (a disk problem never touches the call)
            self._error = repr(exc)[:300]
            logger.error(f"[{self.interview_id}] candidate audio capture failed: {self._error}")
            while True:  # keep draining so the tap never sees a full queue forever
                try:
                    if self._queue.get(timeout=0.5) is _STOP:
                        break
                except queue.Empty:
                    if self._closed:
                        break
        finally:
            if wav is not None:
                try:
                    wav.close()  # writes the final header
                except Exception as exc:  # noqa: BLE001
                    self._error = self._error or repr(exc)[:300]

    def _to_16k_mono(self, chunk: _Chunk) -> bytes:
        pcm = chunk.pcm
        if chunk.channels == 2:
            pcm = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
        if chunk.sample_rate != SAMPLE_RATE:
            pcm, self._ratecv_state = audioop.ratecv(pcm, SAMPLE_WIDTH, 1, chunk.sample_rate, SAMPLE_RATE,
                                                     self._ratecv_state)
            self.stats["resampled_chunks"] += 1
        return pcm

    # -- the end ----------------------------------------------------------------------------

    def close(self, timeout: float = 10.0) -> None:
        """Stop and finish the WAV. Blocking (call it with asyncio.to_thread)."""
        self._closed = True
        if self._thread is None:
            return
        while True:
            try:
                self._queue.put(_STOP, timeout=timeout)
                break
            except queue.Full:
                if not self._thread.is_alive():
                    break
        self._thread.join(timeout)

    def write_manifest(self, mitra_speaking: list[tuple[float, float]]) -> Path | None:
        """The CaptureManifest, last and atomically. Without a complete WAV there is no
        candidate_audio (and an unfinished WAV is removed); Mitra's times are kept."""
        from shared.interview_contracts import CandidateAudioCapture as AudioRecord
        from shared.interview_contracts import CaptureManifest, TimeRange

        audio = None
        complete = self._thread is not None and not self._thread.is_alive() and self._error is None \
            and self._first_at is not None and self.wav_path.exists()
        if complete:
            audio = AudioRecord(relative_path=f"{self.interview_id}/candidate.wav", sample_rate_hz=SAMPLE_RATE,
                                channels=1, sample_format="s16le", start_seconds=round(self._first_at, 3),
                                duration_seconds=round(self._written_samples / SAMPLE_RATE, 3),
                                bytes=self.wav_path.stat().st_size)
        elif self.wav_path.exists():
            self.wav_path.unlink()
        spans = [TimeRange(start_seconds=round(max(0.0, a), 3), end_seconds=round(max(a, b, 0.0), 3))
                 for a, b in mitra_speaking if a is not None and b is not None]
        manifest = CaptureManifest(interview_id=self.interview_id, candidate_audio=audio, mitra_speaking=spans)
        folder = self.manifest_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".manifest.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(manifest.model_dump(mode="json"), indent=1))
        os.replace(tmp, self.manifest_path)
        return self.manifest_path

    def summary(self) -> dict:
        return {**self.stats, "tap_seconds_total": round(self.stats["tap_seconds_total"], 6),
                "tap_seconds_max": round(self.stats["tap_seconds_max"], 6),
                "written_seconds": round(self._written_samples / SAMPLE_RATE, 3),
                "start_seconds": None if self._first_at is None else round(self._first_at, 3),
                "error": self._error, "queue_chunks": self.queue_chunks}


def mitra_speaking_from(transcript) -> list[tuple[float, float]]:
    """Mitra's spoken times from the built transcript (the interview clock)."""
    from shared.contracts import Speaker

    spans = []
    for turn in transcript.turns:
        if turn.speaker == Speaker.INTERVIEWER and turn.audio_start_seconds is not None \
                and turn.audio_end_seconds is not None:
            spans.append((turn.audio_start_seconds, turn.audio_end_seconds))
    return spans
