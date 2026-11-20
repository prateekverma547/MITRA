"""Reading the candidate's lips for the hearing check, from their camera.

Off unless `CAMERA_HEARING_CHECK` is set (bot/config.py). When on, this observer:

- takes the candidate's video frames (the first camera that sends video is treated
  as the candidate, since the bot sends none of its own),
- samples them at a few frames a second,
- measures the lips with MediaPipe face mesh, off the event loop,
- passes the judged state to the hearing monitor.

It never talks to the candidate and never changes the pipeline. It is an observer,
so it sees frames without sitting in the chain. Any failure in the camera path is
logged and, after repeated errors, switches the camera off for the rest of the call,
so video can never take the interview down.

The measurement itself is in bot/lip_activity.py, which imports no vision library.
"""

import asyncio
import importlib.util
import time

import numpy as np
from loguru import logger
from pipecat.frames.frames import UserImageRawFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed

from bot.lip_activity import LipActivityDetector, LipSample, sample_from_landmarks

#: Frames per second the lips are measured at. Enough for the hold rule (0.5 s) and
#: far below the camera's own rate, so the CPU cost stays bounded.
SAMPLE_HZ = 5.0

#: After this many failures the camera is switched off for the call.
MAX_ERRORS = 20


def mediapipe_available() -> bool:
    """Whether the optional vision library is installed. Checked without importing it."""
    return importlib.util.find_spec("mediapipe") is not None


def mediapipe_landmarks():
    """Return a function mapping an RGB image to face-mesh landmarks, or None if no face.

    Imported here, not at module load, because MediaPipe is an optional dependency.
    """
    import mediapipe as mp

    mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    def run(rgb: np.ndarray):
        result = mesh.process(rgb)
        if not result.multi_face_landmarks:
            return None
        return result.multi_face_landmarks[0].landmark

    return run


def _to_rgb(frame: UserImageRawFrame) -> np.ndarray:
    width, height = frame.size
    fmt = (frame.format or "RGB").upper()
    channels = 4 if fmt in ("RGBA", "BGRA") else 3
    arr = np.frombuffer(frame.image, dtype=np.uint8).reshape(height, width, channels)
    if fmt.startswith("BGR"):
        arr = arr[:, :, ::-1]
    return np.ascontiguousarray(arr[:, :, :3])


class CameraMouthObserver(BaseObserver):
    def __init__(
        self,
        *,
        hearing,
        landmarks=None,
        session_id: str = "",
        sample_hz: float = SAMPLE_HZ,
        clock=time.monotonic,
    ):
        super().__init__()
        self._hearing = hearing
        self._landmarks = landmarks
        self._session_id = session_id
        self._period = 1.0 / sample_hz
        self._clock = clock
        self._detector = LipActivityDetector()
        self._candidate_id: str | None = None
        self._last_sample_at: float | None = None
        self._busy = False
        self._errors = 0
        self._disabled = False
        self._seen: set[int] = set()

    @property
    def disabled(self) -> bool:
        return self._disabled

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if self._disabled or not isinstance(frame, UserImageRawFrame):
            return
        if frame.id in self._seen:
            return  # the same frame is pushed by more than one processor
        self._seen.add(frame.id)

        if self._candidate_id is None:
            self._candidate_id = frame.user_id
            logger.info(f"[{self._session_id}] camera hearing check watching participant {frame.user_id}")
        if frame.user_id != self._candidate_id:
            return

        now = self._clock()
        # The small tolerance stops float rounding (0.2 s computed as 0.19999 s)
        # from skipping a sample that is due.
        if self._last_sample_at is not None and now - self._last_sample_at < self._period - 1e-3:
            return
        if self._busy:
            return  # one measurement at a time; MediaPipe is not built for concurrent calls
        self._last_sample_at = now
        self._busy = True
        try:
            sample = await asyncio.to_thread(self._measure, frame, now)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return
        finally:
            self._busy = False

        state = self._detector.push(sample)
        self._hearing.note_mouth(state)

    def _measure(self, frame: UserImageRawFrame, now: float) -> LipSample:
        rgb = _to_rgb(frame)
        marks = self._landmarks(rgb)
        if marks is None:
            return LipSample(t=now, lip_opening=None)
        return sample_from_landmarks(now, marks)

    def _fail(self, exc: Exception) -> None:
        self._errors += 1
        logger.warning(f"[{self._session_id}] camera hearing check failed ({self._errors}): {exc!r}")
        if self._errors >= MAX_ERRORS:
            self._disabled = True
            logger.error(f"[{self._session_id}] camera hearing check switched off after {self._errors} errors")
