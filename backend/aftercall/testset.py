"""Constructed test cases for the second-voice check, with known answers.

Every case is a stand-in for the candidate's own track (16 kHz mono): Mitra's
turns are silence on that track (or Mitra's voice leaking back through the
candidate's speakers), the candidate's answers are speech with thinking pauses,
and sometimes another person speaks. All cases are CONSTRUCTED: real recorded
human speech (LibriSpeech, CC BY 4.0) or synthetic voices, mixed in code.

Used by the measurements (D-50), by the unit tests and by the smoke stage. The
sources are passed in, so the same code builds the committed fixtures' cases and
the larger local study.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, resample_poly, sosfilt

SR = 16000

KINDS = (
    "one_speaker",        # 0: the candidate alone
    "two_quiet",          # 1: another person, about 12 dB quieter and further away, in a pause
    "two_clear",          # 1: another person, as loud as the candidate, in a pause
    "two_overlap",        # 1: another person, quieter, speaking OVER the candidate (hard)
    "noise_echo",         # 0: the candidate alone, in a reverberant room with steady noise
    "mitra_leak",         # 0: Mitra's voice from the candidate's speakers into the microphone
    "mitra_leak_loud",    # 0: the same, louder (echo cancellation off)
    "long_silences",      # 0: the candidate alone, long silences and thinking pauses
    "two_mid_answer",     # 1: another person, as loud as the candidate, in a pause MID-answer
)                         #    (added after the 1.0 s echo tail: D-53; appended so the others keep their seeds)
SECOND_VOICE = {"two_quiet", "two_clear", "two_overlap", "two_mid_answer"}


@dataclass
class Case:
    kind: str
    audio: np.ndarray
    second_voice: bool
    #: Where another person really speaks (seconds into the file).
    truth: list[tuple[float, float]] = field(default_factory=list)
    #: Where Mitra's voice leaks (seconds), for reporting.
    leak: list[tuple[float, float]] = field(default_factory=list)
    #: When Mitra is speaking (seconds): the bot knows these from its own turns.
    mitra_turns: list[tuple[float, float]] = field(default_factory=list)


def load(path: Path) -> np.ndarray:
    """Any mono 16-bit WAV, resampled to 16 kHz, as float."""
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch != 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        g = np.gcd(sr, SR)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def write(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def active_rms(x: np.ndarray) -> float:
    """RMS over the louder half of 20 ms frames (speech, not the silences)."""
    f = x[: len(x) // 320 * 320].reshape(-1, 320)
    e = np.sqrt((f ** 2).mean(axis=1))
    e = e[e > 0]
    return float(np.sqrt(np.mean(np.sort(e)[len(e) // 2:] ** 2))) if e.size else 0.0


def at_level(x: np.ndarray, rms: float) -> np.ndarray:
    a = active_rms(x)
    return x * (rms / a) if a > 0 else x


def room(x: np.ndarray, rt60: float, rng: np.random.Generator, direct: float = 1.0) -> np.ndarray:
    """A synthetic room: a direct path plus exponentially decaying noise (reverberation time rt60)."""
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60) * 0.25
    ir = np.concatenate([[direct], np.zeros(int(0.004 * SR)), tail])
    y = fftconvolve(x, ir)[: len(x)]
    return y.astype(np.float32)


def through_speakers(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Mitra's voice as the candidate's microphone hears it from laptop speakers:
    band-limited (200-6,000 Hz), about 80 ms late, in the room."""
    sos = butter(4, [200, 6000], btype="band", fs=SR, output="sos")
    y = sosfilt(sos, x)
    y = np.concatenate([np.zeros(int(0.08 * SR)), y])[: len(x)]
    return room(y.astype(np.float32), 0.35, rng, direct=0.6)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    f = np.fft.rfft(rng.standard_normal(n))
    f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
    y = np.fft.irfft(f, n)
    return (y / np.std(y)).astype(np.float32)


def _answer(pieces: list[np.ndarray], rng: np.random.Generator, seconds: float, pauses=(0.4, 1.8)) -> np.ndarray:
    """An answer: speech pieces with thinking pauses, about `seconds` long."""
    out, total = [], 0.0
    i = int(rng.integers(len(pieces)))
    while total < seconds:
        p = pieces[i % len(pieces)]
        a = int(rng.integers(0, max(len(p) - int(3 * SR), 1)))
        seg = p[a: a + int(rng.uniform(2.5, 6.0) * SR)]
        out += [seg, np.zeros(int(rng.uniform(*pauses) * SR), dtype=np.float32)]
        total += (len(seg) + len(out[-1])) / SR
        i += 1
    return np.concatenate(out)


def build(kind: str, candidate: list[np.ndarray], other: list[np.ndarray], mitra: list[np.ndarray],
          seed: int = 0, seconds: float = 90.0) -> Case:
    """One constructed track of about `seconds`, with its known answer."""
    if kind not in KINDS:
        raise ValueError(kind)
    rng = np.random.default_rng(seed)
    level = 0.08  # the candidate's speech level (about -22 dBFS)
    parts: list[tuple[str, np.ndarray]] = []
    t = 0.0
    turn = 0
    long_gaps = kind == "long_silences"
    while t < seconds:
        gap = rng.uniform(20, 40) if long_gaps and turn % 2 == 1 else rng.uniform(3, 7)  # Mitra speaking
        parts.append(("mitra", np.zeros(int(gap * SR), dtype=np.float32)))
        if kind in ("two_quiet", "two_clear") and turn in (1, 3):
            parts.append(("other", np.zeros(0, dtype=np.float32)))  # placeholder, filled below
        ans = at_level(_answer(candidate, rng, rng.uniform(10, 18), pauses=(1.0, 4.0) if long_gaps else (0.4, 1.8)),
                       level)
        if kind == "two_mid_answer" and turn in (1, 3):
            k = int(len(ans) * 0.4)
            parts += [("candidate", ans[:k]), ("other", np.zeros(0, dtype=np.float32)), ("candidate", ans[k:])]
        else:
            parts.append(("candidate", ans))
        t += gap + len(ans) / SR
        turn += 1
    x_parts, truth, leak, turns = [], [], [], []
    cursor = 0
    for role, seg in parts:
        if role == "other":
            o = other[int(rng.integers(len(other)))]
            a = int(rng.integers(0, max(len(o) - int(4 * SR), 1)))
            seg = o[a: a + int(rng.uniform(2.5, 4.0) * SR)]
            if kind == "two_quiet":
                seg = room(at_level(seg, level * 0.25), 0.5, rng, direct=0.5)  # about -12 dB, further away
            else:
                seg = at_level(seg, level)
            seg = np.concatenate([seg, np.zeros(int(0.6 * SR), dtype=np.float32)])
            truth.append((cursor / SR, (cursor + len(seg) - int(0.6 * SR)) / SR))
        if role == "mitra":
            turns.append((cursor / SR, (cursor + len(seg)) / SR))
        if role == "mitra" and kind in ("mitra_leak", "mitra_leak_loud") and len(seg) > SR:
            m = mitra[int(rng.integers(len(mitra)))]
            n = min(len(seg) - int(0.5 * SR), len(m))
            gain = level * (0.1 if kind == "mitra_leak" else 0.32)  # about -20 dB / -10 dB
            seg = seg.copy()
            seg[: n] += at_level(through_speakers(m[:n], rng), gain)
            leak.append((cursor / SR, (cursor + n) / SR))
        x_parts.append(seg)
        cursor += len(seg)
    x = np.concatenate(x_parts).astype(np.float32)
    if kind == "two_overlap":
        # another person speaking over the candidate, about 12 dB quieter, twice
        for k in range(2):
            o = other[int(rng.integers(len(other)))]
            seg = room(at_level(o[: int(3.5 * SR)], level * 0.25), 0.5, rng, direct=0.5)
            s = int(len(x) * (0.3 + 0.4 * k))
            x[s: s + len(seg)] += seg[: len(x) - s]
            truth.append((s / SR, (s + len(seg)) / SR))
    if kind == "noise_echo":
        x = room(x, 0.6, rng, direct=0.7)
        x = x + pink(len(x), rng) * (active_rms(x) / 10 ** (15 / 20))  # 15 dB below the speech
    else:
        x = x + rng.standard_normal(len(x)).astype(np.float32) * 1e-4  # a quiet microphone floor
    return Case(kind=kind, audio=x.astype(np.float32), second_voice=kind in SECOND_VOICE, truth=truth, leak=leak,
                mitra_turns=turns)


def overlaps(found: list[tuple[float, float]], truth: list[tuple[float, float]]) -> int:
    """How many true ranges at least one found range touches."""
    return sum(1 for a, b in truth if any(fa < b and fb > a for fa, fb in found))
