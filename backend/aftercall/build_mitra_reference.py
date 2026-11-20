"""Build mitra_voice.json: reference embeddings of Mitra's own voice.

Mitra speaks with the OpenAI Realtime voice named in REALTIME_VOICE (default
"cedar", bot/services/realtime.py). When the candidate's echo cancellation
fails, that voice reaches their microphone and their own track; the second-voice
check must not report it as another person. This file holds a few embeddings of
that voice (k-means centres of 1.5 s speech windows), never any audio.

    uv run python -m aftercall.build_mitra_reference --voice cedar OUT.json clip1.wav clip2.wav ...

Built 2026-11-19 from Mitra's real replies saved by Session A
(track_i_notes/A_checks/ending_audio/{closing_1,closing_2,closing_question_1}/mitra_*.wav,
24 kHz). Other Mitra clips (closing_3, hardstop_1, skip_1, round1) are kept out of
it on purpose: the leak tests use them, so the exclusion is never tested on the
audio it was built from. Rebuild when the voice changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from aftercall import models, second_voice

K = 8


def load(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch != 1 or width != 2:
        raise SystemExit(f"{path}: needs mono 16-bit")
    if sr != second_voice.SR:
        g = np.gcd(sr, second_voice.SR)
        x = resample_poly(x, second_voice.SR // g, sr // g).astype(np.float32)
    return x


def windows(x: np.ndarray) -> list[np.ndarray]:
    probs = second_voice.speech_probabilities(x)
    win, hop = int(second_voice.WINDOW_S * second_voice.SR), int(second_voice.HOP_S * second_voice.SR)
    out = []
    for s in range(0, max(len(x) - win, 0) + 1, hop):
        a, b = s // second_voice.VAD_CHUNK, (s + win) // second_voice.VAD_CHUNK
        if b > a and (probs[a:b] >= second_voice.VAD_SPEECH).mean() >= second_voice.MIN_SPEECH_FRACTION:
            out.append(x[s:s + win])
    return out


def kmeans(e: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    c = e[rng.choice(len(e), size=min(k, len(e)), replace=False)]
    for _ in range(50):
        lab = (e @ c.T).argmax(axis=1)
        c = np.stack([e[lab == j].mean(axis=0) if (lab == j).any() else c[j] for j in range(len(c))])
        c /= np.linalg.norm(c, axis=1, keepdims=True)
    return c


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", required=True)
    ap.add_argument("out", type=Path)
    ap.add_argument("clips", type=Path, nargs="+")
    a = ap.parse_args(argv)
    segs, sources = [], []
    for p in a.clips:
        w = windows(load(p))
        segs += w
        sources.append({"file": p.name, "folder": p.parent.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        "windows": len(w)})
    emb = second_voice.embed(segs)
    centres = kmeans(emb, K)
    data = {
        "voice": a.voice,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "speaker_model_sha256": models.SPEAKER.sha256,
        "method_version": second_voice.METHOD_VERSION,
        "windows": len(segs),
        "sources": sources,
        "embeddings": [[round(float(v), 6) for v in c] for c in centres],
    }
    a.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{len(segs)} windows from {len(a.clips)} clips -> {len(centres)} reference embeddings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
