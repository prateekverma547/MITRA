"""The second-voice check: does a voice other than the candidate's appear on the
candidate's own audio track?

Input: the candidate's track as the bot saved it (16 kHz mono 16-bit, D-33 to
D-35). It holds the candidate alone, apart from whatever reached their
microphone: a person beside them, or Mitra's own voice leaking from their
speakers when echo cancellation fails.

Method (open source, on the processor, one thread):
1. Speech is found with Silero VAD (models.VAD), in 32 ms steps.
2. The track is cut into 1.5 s windows every 0.5 s; a window with at least 60%
   speech gets a speaker embedding from WeSpeaker ResNet34-LM (models.SPEAKER;
   features.py computes its input exactly as sherpa-onnx does).
3. The candidate's voice is the track's dominant voice: the centre of the
   windows, re-centred three times on the half closest to it. (It is their own
   track, so they speak most; when there is too little speech to say so, the
   check is skipped and says why.)
4. A window whose cosine similarity to the candidate's voice is below
   OTHER_BELOW is a voice other than the candidate's, unless Mitra may be
   speaking in it. Mitra's voice leaking from the candidate's speakers into
   their microphone is not a second person, and it can only happen while
   Mitra speaks: every window that overlaps one of Mitra's turns (plus
   MITRA_TAIL_S of echo) is left out. As a backup, a window whose similarity to
   Mitra's reference voice (mitra_voice.json) is at least MITRA_VOICE_AT_LEAST
   is left out too. Telling Mitra apart by voice alone was measured unsafe
   (D-51), so WITHOUT Mitra's speaking times the check is skipped, never run.
5. Neighbouring flagged windows make a range; a range needs MIN_WINDOWS windows.

The numbers were chosen from the labelled test set (D-50, D-51); change them only
with a new measurement. Known limitation (accepted, D-52): on held-out constructed
speakers about half of second voices, clear or quiet, are missed. A quiet result
does not mean nobody else spoke.

Output: a SecondVoiceSignal only when at least one range is found. Its wording
describes what may be heard, never why; the ranges are on the interview clock.
Nothing here reads, keeps or logs what anyone said.
"""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from aftercall import models
from aftercall.features import fbank

SR = 16000
METHOD = ("voice embeddings against the track's main voice, Mitra's turns left out "
          "(WeSpeaker ResNet34-LM, Silero VAD)")
#: Bump whenever the method, a model or a number below changes.
METHOD_VERSION = "1.0.0"  # unchanged by reading in pieces: same numbers (D-63)

VAD_CHUNK = 512
VAD_CONTEXT = 64
VAD_SPEECH = 0.5
WINDOW_S = 1.5
HOP_S = 0.5
MIN_SPEECH_FRACTION = 0.6
#: Below this many speech windows (about 10 s of speech) the main voice is not
#: known well enough to compare against.
MIN_SPEECH_WINDOWS = 20

#: Chosen from the measurements (D-51).
OTHER_BELOW = 0.50
MIN_WINDOWS = 2
MITRA_TAIL_S = 1.0  # bot playback end to the leak ending at the mic: unmeasured; 0.3-0.7 s estimated (D-53)
MITRA_VOICE_AT_LEAST = 0.70

MITRA_REFERENCE = Path(__file__).resolve().parent / "mitra_voice.json"


NO_MITRA_TIMES = ("Mitra's speaking times are not available, so Mitra's own voice "
                  "could not be told apart from another person's")


class CheckSkipped(Exception):
    """The check cannot say anything about this track; the message says why."""


class Paused(Exception):
    """The runner was paused while this check ran; it will be run again."""


@dataclass
class Window:
    start: float
    end: float
    candidate: float  # cosine similarity to the main voice
    mitra: float      # best cosine similarity to Mitra's reference voice


@dataclass
class Result:
    ranges: list[tuple[float, float]]
    confidence: float
    windows: list[Window] = field(default_factory=list)
    speech_seconds: float = 0.0
    audio_seconds: float = 0.0
    mitra_excluded_windows: int = 0


# -- input -----------------------------------------------------------------------------------


def read_wav(path: Path) -> np.ndarray:
    """The candidate's track as float samples. Refuses anything but 16 kHz mono 16-bit."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            raise CheckSkipped(
                f"the audio is {w.getframerate()} Hz, {w.getnchannels()} channel(s), "
                f"{8 * w.getsampwidth()}-bit; the check needs 16 kHz mono 16-bit")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


# -- the models ----------------------------------------------------------------------------------


def speech_probabilities(x: np.ndarray, should_stop: Callable[[], bool] = lambda: False) -> np.ndarray:
    """Silero VAD speech probability per 512-sample (32 ms) step, the way pipecat calls it."""
    sess = models.session(models.VAD)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, VAD_CONTEXT), dtype=np.float32)
    sr = np.array(SR, dtype=np.int64)
    n = len(x) // VAD_CHUNK
    probs = np.zeros(n, dtype=np.float32)
    for i in range(n):
        if i % 3000 == 0 and should_stop():  # about every 96 s of audio
            raise Paused()
        chunk = np.concatenate([context, x[i * VAD_CHUNK:(i + 1) * VAD_CHUNK][None, :]], axis=1)
        out, state = sess.run(None, {"input": chunk, "state": state, "sr": sr})
        probs[i] = float(out[0][0])
        context = chunk[:, -VAD_CONTEXT:]
    return probs


def embed(segments: list[np.ndarray], should_stop: Callable[[], bool] = lambda: False, batch: int = 16) -> np.ndarray:
    """One unit-length 256-number speaker embedding per segment (equal lengths)."""
    sess = models.session(models.SPEAKER)
    out = []
    for i in range(0, len(segments), batch):
        if should_stop():
            raise Paused()
        feats = np.stack([fbank(s) for s in segments[i:i + batch]])
        e = sess.run(None, {"feats": feats})[0]
        out.append(e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9))
    return np.concatenate(out) if out else np.zeros((0, 256), dtype=np.float32)


def mitra_reference() -> np.ndarray:
    data = json.loads(MITRA_REFERENCE.read_text())
    ref = np.asarray(data["embeddings"], dtype=np.float32)
    return ref / np.linalg.norm(ref, axis=1, keepdims=True)


# -- reading in pieces ----------------------------------------------------------------------------
#
# The track is never held whole (D-62). Chunks of CHUNK_S seconds are read from the file
# and analysed in order; only what later steps need is kept:
# - Silero VAD runs over the chunks as one stream: its state and 64-sample context are
#   carried from chunk to chunk, and samples short of a 512-sample step wait for the next
#   chunk, so every step sees exactly the samples it would see on the whole track. Its
#   probabilities (4 bytes per 32 ms) are kept.
# - A 1.5 s window can start in one chunk and end in the next, so the samples from the
#   first window not yet decided onwards are carried over (the overlap: less than one
#   window, 1.5 s). A window is decided once the VAD has covered its last step.
# - Kept windows are embedded 16 at a time, in the same order and the same groups as
#   before, so the numbers are bit-for-bit the same (D-63); only embeddings are kept.

#: Seconds of audio read and analysed at a time (D-62: chosen from measurements).
CHUNK_S = 15.0
#: Windows embedded together (D-62). Kept at 16, the groups the check has always used,
#: so every number stays bit-identical (D-63: 213 of 213). Smaller groups need less
#: memory (15-minute track: 285 MB at 16, 170 at 4) and decide every case the same,
#: but the last window of a track, embedded alone when the count leaves one over,
#: takes onnxruntime's other path, so 27 of 213 cases differ in the last bits
#: (at most 5.7e-7). A choice for Karandeep (D-64).
EMBED_BATCH = 16


class _Stream:
    """Speech steps, windows and embeddings, fed one chunk of samples at a time."""

    def __init__(self, should_stop: Callable[[], bool]):
        self.should_stop = should_stop
        self.vad = models.session(models.VAD)
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, VAD_CONTEXT), dtype=np.float32)
        self.sr = np.array(SR, dtype=np.int64)
        self.pending = np.zeros(0, dtype=np.float32)  # samples short of a VAD step
        self.probs: list[float] = []
        self.speech = np.zeros(0, dtype=bool)          # probs >= VAD_SPEECH, built up as they come
        self.buf = np.zeros(0, dtype=np.float32)      # samples from buf_start on
        self.buf_start = 0
        self.total = 0
        self.next_win = 0
        self.win, self.hop = int(WINDOW_S * SR), int(HOP_S * SR)
        self.keep: list[int] = []
        self.segs: list[np.ndarray] = []
        self.embs: list[np.ndarray] = []

    def _vad(self, x: np.ndarray) -> None:
        x = np.concatenate([self.pending, x]) if self.pending.size else x
        n = len(x) // VAD_CHUNK
        for i in range(n):
            if len(self.probs) % 3000 == 0 and self.should_stop():  # about every 96 s of audio
                raise Paused()
            step = np.concatenate([self.context, x[i * VAD_CHUNK:(i + 1) * VAD_CHUNK][None, :]], axis=1)
            out, self.state = self.vad.run(None, {"input": step, "state": self.state, "sr": self.sr})
            self.probs.append(float(out[0][0]))
            self.context = step[:, -VAD_CONTEXT:]
        self.pending = x[n * VAD_CHUNK:].copy()

    def _embed_full_batches(self, final: bool = False) -> None:
        while len(self.segs) >= EMBED_BATCH or (final and self.segs):
            batch, self.segs = self.segs[:EMBED_BATCH], self.segs[EMBED_BATCH:]
            self.embs.append(embed(batch, self.should_stop, batch=EMBED_BATCH))

    def feed(self, chunk: np.ndarray) -> None:
        if self.should_stop():
            raise Paused()
        self._vad(chunk)
        self.buf = np.concatenate([self.buf, chunk])
        self.total += len(chunk)
        if len(self.speech) < len(self.probs):  # each probability converted once
            new = np.asarray(self.probs[len(self.speech):], dtype=np.float32) >= VAD_SPEECH
            self.speech = np.concatenate([self.speech, new])
        speech = self.speech
        while self.next_win + self.win <= self.total and (self.next_win + self.win) // VAD_CHUNK <= len(self.probs):
            s = self.next_win
            if s < self.buf_start:  # its samples were not carried over (never, unless the overlap is dropped)
                self.next_win += self.hop
                continue
            a, b = s // VAD_CHUNK, (s + self.win) // VAD_CHUNK
            if b > a and float(speech[a:b].mean()) >= MIN_SPEECH_FRACTION:
                self.keep.append(s)
                self.segs.append(self.buf[s - self.buf_start:s - self.buf_start + self.win].copy())
            self.next_win += self.hop
        self._embed_full_batches()
        # carry over only the samples a window not yet decided still needs
        start = self._carry_from()
        self.buf = self.buf[start - self.buf_start:]
        self.buf_start = start

    def _carry_from(self) -> int:
        return self.next_win

    def finish(self) -> Measured:
        self._embed_full_batches(final=True)
        speech_s = float(self.speech.sum() * VAD_CHUNK / SR)
        if len(self.keep) < MIN_SPEECH_WINDOWS:
            raise CheckSkipped(
                f"too little speech on the candidate's track to compare voices "
                f"({len(self.keep)} usable 1.5 s windows; at least {MIN_SPEECH_WINDOWS} are needed)")
        emb = np.concatenate(self.embs) if self.embs else np.zeros((0, 256), dtype=np.float32)
        mitra = (emb @ mitra_reference().T).max(axis=1)
        return Measured(np.asarray(self.keep, dtype=np.float64) / SR, emb, mitra, speech_s, self.total / SR)


# -- the decision ---------------------------------------------------------------------------------


def main_voice(emb: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """The dominant voice: the centre of the usable windows, re-centred on the closest half."""
    pts = emb[usable] if usable.any() else emb
    c = pts.mean(axis=0)
    for _ in range(3):
        c = c / np.linalg.norm(c)
        sims = pts @ c
        c = pts[sims >= np.median(sims)].mean(axis=0)
    return c / np.linalg.norm(c)


@dataclass
class Measured:
    """What the models said about one track; the decision is made on this."""

    starts: np.ndarray       # window start, seconds into the file
    emb: np.ndarray          # unit embeddings, one per usable window
    mitra: np.ndarray        # best cosine similarity to Mitra's reference, per window
    speech_seconds: float
    audio_seconds: float


def _wav_chunks(path: Path, seconds: float):
    """The file's samples as float, CHUNK_S seconds at a time. Refuses anything but 16 kHz mono 16-bit."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            raise CheckSkipped(
                f"the audio is {w.getframerate()} Hz, {w.getnchannels()} channel(s), "
                f"{8 * w.getsampwidth()}-bit; the check needs 16 kHz mono 16-bit")
        n = max(int(seconds * SR), 1)
        while raw := w.readframes(n):
            yield np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _array_chunks(x: np.ndarray, seconds: float):
    n = max(int(seconds * SR), 1)
    for i in range(0, len(x), n):
        yield x[i:i + n]


def _measure(chunks, should_stop: Callable[[], bool]) -> Measured:
    stream = _Stream(should_stop)
    for chunk in chunks:
        stream.feed(chunk)
    return stream.finish()


def measure(x: np.ndarray, should_stop: Callable[[], bool] = lambda: False) -> Measured:
    """Speech, windows and embeddings for a track already in memory (tests, the test set)."""
    return _measure(_array_chunks(np.asarray(x, dtype=np.float32), CHUNK_S), should_stop)


def measure_file(path: Path, should_stop: Callable[[], bool] = lambda: False) -> Measured:
    """The same, read from the WAV file in pieces: the whole track is never in memory (D-62)."""
    return _measure(_wav_chunks(Path(path), CHUNK_S), should_stop)


def decide(m: Measured, mitra_turns: list[tuple[float, float]] | None, *, exclude_mitra: bool = True,
           other_below: float = OTHER_BELOW, min_windows: int = MIN_WINDOWS, mitra_tail_s: float = MITRA_TAIL_S,
           mitra_voice_at_least: float = MITRA_VOICE_AT_LEAST) -> Result:
    """Ranges where a voice other than the track's main voice is heard.

    `mitra_turns` are Mitra's speaking times in seconds into the file. With
    exclude_mitra False (measurement only) nothing is left out.
    """
    if exclude_mitra and mitra_turns is None:
        raise CheckSkipped(NO_MITRA_TIMES)
    n = len(m.starts)
    if exclude_mitra:
        ends = m.starts + WINDOW_S
        in_turn = np.zeros(n, dtype=bool)
        for a, b in mitra_turns:
            in_turn |= (m.starts < b + mitra_tail_s) & (ends > a)
        excluded = in_turn | (m.mitra >= mitra_voice_at_least)
    else:
        excluded = np.zeros(n, dtype=bool)
    centre = main_voice(m.emb, ~excluded)
    cand = m.emb @ centre
    flagged = (cand < other_below) & ~excluded
    windows = [Window(float(s), float(s) + WINDOW_S, float(c), float(v)) for s, c, v in zip(m.starts, cand, m.mitra)]
    # neighbouring flagged windows (overlapping, or the next hop) make one range
    ranges, run = [], []
    for i in range(n):
        if flagged[i] and run and windows[i].start - windows[run[-1]].start <= HOP_S + 1e-6:
            run.append(i)
            continue
        if len(run) >= min_windows:
            ranges.append(run)
        run = [i] if flagged[i] else []
    if len(run) >= min_windows:
        ranges.append(run)
    spans = [(windows[r[0]].start, windows[r[-1]].end) for r in ranges]
    # The method's own number: how far below the line the clearest range sits,
    # from 0 (at the line) to 1 (as unlike the main voice as two strangers, cosine 0).
    confidence = 0.0
    if ranges:
        depth = max(float(np.mean([other_below - cand[i] for i in r])) for r in ranges)
        confidence = round(min(1.0, max(0.0, depth / other_below)), 3)
    return Result(ranges=spans, confidence=confidence, windows=windows, speech_seconds=m.speech_seconds,
                  audio_seconds=m.audio_seconds,
                  mitra_excluded_windows=int((excluded & (cand < other_below)).sum()))


def analyse(x: np.ndarray, mitra_turns: list[tuple[float, float]] | None, *,
            should_stop: Callable[[], bool] = lambda: False, **params) -> Result:
    """Find stretches of a voice other than the track's main voice. Times are seconds into the file."""
    if params.get("exclude_mitra", True) and mitra_turns is None:
        raise CheckSkipped(NO_MITRA_TIMES)
    return decide(measure(x, should_stop), mitra_turns, **params)


def analyse_file(path: Path, mitra_turns: list[tuple[float, float]] | None, *,
                 should_stop: Callable[[], bool] = lambda: False, **params) -> Result:
    """analyse() for the candidate's WAV, read in pieces."""
    if params.get("exclude_mitra", True) and mitra_turns is None:
        raise CheckSkipped(NO_MITRA_TIMES)
    return decide(measure_file(path, should_stop), mitra_turns, **params)


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def wording(ranges: list[tuple[float, float]]) -> str:
    """Neutral: what may be heard and where. Never why, never a judgement."""
    first = ", ".join(f"{_clock(a)}-{_clock(b)}" for a, b in ranges[:3])
    more = f" and {len(ranges) - 3} more place(s)" if len(ranges) > 3 else ""
    return f"A voice other than the candidate's may be audible at {first}{more}. Listen before drawing any conclusion."
