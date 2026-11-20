"""Telling from the candidate's camera whether their mouth is moving.

Prototype origin: lip-opening motion separated continuous speech from silence well
on a still face (AUC 0.99 on one clip), but head movement alone raised the same
measure, so the signal is only trusted while the head is steady.

This module is pure: it takes landmark numbers and returns a state. It does not
import MediaPipe or Pipecat, so it runs and is tested without either. Reading the
camera is a separate step, and it is not wired into the pipeline yet.

The state is three-valued on purpose. `UNKNOWN` (no face, or the head is moving) is
not `SILENT`: a candidate whose head is moving must never be read as having gone
quiet.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

#: MediaPipe face-mesh landmark indices used for the measure.
LIP_UPPER_INNER = 13
LIP_LOWER_INNER = 14
FOREHEAD = 10
CHIN = 152
NOSE_TIP = 1


class MouthState(Enum):
    SPEAKING = "speaking"
    SILENT = "silent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LipSample:
    """One camera frame, reduced to the numbers the detector needs.

    All positions are normalised image coordinates, as MediaPipe reports them.
    `lip_opening` and `face_height` are None or 0 when no face was found.
    """

    t: float
    lip_opening: float | None
    nose_x: float = 0.0
    nose_y: float = 0.0
    face_height: float = 0.0


def sample_from_landmarks(t: float, landmarks) -> LipSample:
    """Build a sample from face-mesh landmarks (any indexable of objects with x, y)."""
    face_h = abs(landmarks[FOREHEAD].y - landmarks[CHIN].y)
    if face_h <= 0:
        return LipSample(t=t, lip_opening=None)
    opening = abs(landmarks[LIP_UPPER_INNER].y - landmarks[LIP_LOWER_INNER].y) / face_h
    nose = landmarks[NOSE_TIP]
    return LipSample(t=t, lip_opening=opening, nose_x=nose.x, nose_y=nose.y, face_height=face_h)


@dataclass(frozen=True)
class LipThresholds:
    """Tuned on two recorded clips, not on a labelled set of interviews.

    `speech_motion`: mean frame-to-frame lip change over the window, above which the
    mouth is judged to be moving. Measured medians: silent 0.0002, speaking 0.0078.

    `head_still`: mean head displacement between samples, relative to face height,
    above which the head is moving and the lip reading is not trusted. It depends on
    the sample rate: the displacement between samples grows as the rate falls. The
    values below were measured on one clip, the 22:43 recording, subsampled to the
    observer's 5 fps. At 5 fps, 0.02 judged 63% of frames at 95% accuracy; at the
    prototype's 20 fps, 0.008 judged 76% at 96%. Re-measure if the rate changes.
    Gating has not yet been tested on silence with head movement.

    `hold_seconds`: how long the mouth must be still before we call it silent.
    Stops a pause between words from flipping the state.
    """

    window_seconds: float = 1.0
    speech_motion: float = 0.002
    head_still: float = 0.02
    hold_seconds: float = 0.5


@dataclass
class LipActivityDetector:
    thresholds: LipThresholds = field(default_factory=LipThresholds)

    def __post_init__(self) -> None:
        self._window: deque[tuple[float, float, float]] = deque()  # (t, lip_change, head_move)
        self._prev: LipSample | None = None
        self._state = MouthState.UNKNOWN
        self._quiet_since: float | None = None

    @property
    def state(self) -> MouthState:
        return self._state

    def push(self, sample: LipSample) -> MouthState:
        """Feed one frame in time order and return the current state."""
        t = sample.t
        if sample.lip_opening is None or sample.face_height <= 0:
            self._prev = None
            self._trim(t)
            return self._decide(t, judgeable=False)

        if self._prev is not None and self._prev.lip_opening is not None:
            lip_change = abs(sample.lip_opening - self._prev.lip_opening)
            head_move = math.hypot(
                sample.nose_x - self._prev.nose_x, sample.nose_y - self._prev.nose_y
            ) / sample.face_height
            self._window.append((t, lip_change, head_move))
        self._prev = sample
        self._trim(t)
        return self._decide(t, judgeable=True)

    def _trim(self, now: float) -> None:
        cutoff = now - self.thresholds.window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def _decide(self, now: float, *, judgeable: bool) -> MouthState:
        th = self.thresholds
        if not judgeable or not self._window:
            return self._hold(now, MouthState.UNKNOWN)

        lip = sum(c for _, c, _ in self._window) / len(self._window)
        head = sum(h for _, _, h in self._window) / len(self._window)
        if head > th.head_still:
            # Head moving: the lip measure is not trustworthy, so do not judge.
            return self._hold(now, MouthState.UNKNOWN)

        if lip >= th.speech_motion:
            self._quiet_since = None
            self._state = MouthState.SPEAKING
            return self._state
        return self._hold(now, MouthState.SILENT)

    def _hold(self, now: float, candidate: MouthState) -> MouthState:
        """Apply the hold rule: leave SPEAKING only after a quiet spell, never flicker."""
        if candidate is MouthState.SILENT and self._state is MouthState.SPEAKING:
            if self._quiet_since is None:
                self._quiet_since = now
            if now - self._quiet_since < self.thresholds.hold_seconds:
                return self._state
        if candidate is not MouthState.SILENT:
            self._quiet_since = None
        self._state = candidate
        return self._state
