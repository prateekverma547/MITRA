"""Log mel filterbank features, the way the speaker model was trained on them.

The WeSpeaker ResNet34 model (models.py) takes 80 log mel filterbank energies per
10 ms frame from samples in the int16 range (its metadata says
`normalize_samples: 0`). sherpa-onnx, which round 1 measured (D-14), computes them
with kaldi-native-fbank and its own feature settings; this is the same arithmetic
in numpy, so the backend needs no new dependency. Checked against sherpa-onnx
2.0.1 on the same audio: the embeddings agree to a cosine of 1.0 (D-48).

Settings (sherpa-onnx's, found by that comparison; Kaldi's defaults differ in the
first two): edges NOT snipped (frames centred, the signal reflected at both
ends), the top mel edge at 7,600 Hz (Nyquist minus 400), 25 ms frames, 10 ms
shift, no dither, DC offset removed, pre-emphasis 0.97, the "povey" window, a
512-point FFT, power spectrum, 80 triangular mel bins from 20 Hz, log with a
floor of the float32 epsilon, no energy term, and NO mean subtraction.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
FRAME = 400  # 25 ms
SHIFT = 160  # 10 ms
FFT = 512
BINS = 80
LOW_HZ = 20.0
HIGH_HZ = SAMPLE_RATE / 2 - 400.0  # sherpa-onnx high_freq = -400
PREEMPH = 0.97
EPS = float(np.finfo(np.float32).eps)


def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_banks() -> np.ndarray:
    """Kaldi's MelBanks: weights over the first FFT/2 bins (the Nyquist bin unused)."""
    n_fft_bins = FFT // 2
    bin_hz = SAMPLE_RATE / FFT
    mel_low, mel_high = _mel(LOW_HZ), _mel(HIGH_HZ)
    delta = (mel_high - mel_low) / (BINS + 1)
    mels = _mel(bin_hz * np.arange(n_fft_bins))
    banks = np.zeros((BINS, n_fft_bins))
    for b in range(BINS):
        left, centre, right = mel_low + b * delta, mel_low + (b + 1) * delta, mel_low + (b + 2) * delta
        up = (mels - left) / (centre - left)
        down = (right - mels) / (right - centre)
        banks[b] = np.where((mels > left) & (mels < right), np.where(mels <= centre, up, down), 0.0)
    return banks


_BANKS = _mel_banks()
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / (FRAME - 1))) ** 0.85


def fbank(samples: np.ndarray) -> np.ndarray:
    """80 log mel energies per 10 ms frame for float samples in [-1, 1] at 16 kHz.

    Returns an array of shape (frames, 80); about one frame per 10 ms of input.
    An empty input gives an empty (0, 80) array.
    """
    x = np.asarray(samples, dtype=np.float64).ravel() * 32768.0
    if x.size == 0:
        return np.zeros((0, BINS), dtype=np.float32)
    n = (x.size + SHIFT // 2) // SHIFT
    if n == 0:
        return np.zeros((0, BINS), dtype=np.float32)
    # Kaldi without snip_edges: frame i starts at i*SHIFT + SHIFT/2 - FRAME/2, and
    # an index outside the signal is reflected (-s-1 before it, 2N-1-s after it).
    starts = np.arange(n) * SHIFT + SHIFT // 2 - FRAME // 2
    idx = starts[:, None] + np.arange(FRAME)[None, :]
    idx = np.where(idx < 0, -idx - 1, idx)
    idx = np.where(idx >= x.size, 2 * x.size - 1 - idx, idx)
    idx = np.clip(idx, 0, x.size - 1)  # only for inputs shorter than one frame
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - PREEMPH), frames[:, 1:] - PREEMPH * frames[:, :-1]], axis=1)
    frames = frames * _WINDOW
    power = np.abs(np.fft.rfft(frames, n=FFT, axis=1)) ** 2
    energies = power[:, : FFT // 2] @ _BANKS.T
    return np.log(np.maximum(energies, EPS)).astype(np.float32)
