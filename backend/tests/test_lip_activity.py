"""The camera's view of whether the candidate's mouth is moving.

Synthetic frames only: no camera, no MediaPipe, no recordings. Each test states the
rule it protects.
"""

import math

from bot.lip_activity import (
    LipActivityDetector,
    LipSample,
    LipThresholds,
    MouthState,
    sample_from_landmarks,
)

FPS = 20.0


def still_face(t, lip=0.01, nose=(0.5, 0.4), face_h=0.5):
    return LipSample(t=t, lip_opening=lip, nose_x=nose[0], nose_y=nose[1], face_height=face_h)


def run(detector, samples):
    states = []
    for s in samples:
        states.append(detector.push(s))
    return states


def test_moving_lips_with_a_still_head_are_speaking():
    det = LipActivityDetector()
    samples = [still_face(i / FPS, lip=0.01 + (0.04 if i % 2 else 0.0)) for i in range(60)]
    states = run(det, samples)
    assert states[-1] is MouthState.SPEAKING


def test_still_lips_with_a_still_head_are_silent():
    det = LipActivityDetector()
    samples = [still_face(i / FPS) for i in range(60)]
    states = run(det, samples)
    assert states[-1] is MouthState.SILENT


def test_head_movement_alone_is_not_read_as_speech():
    """Regression: head movement raised the lip measure by itself. While the head is
    moving the detector must not report speaking, and must not report silent either."""
    det = LipActivityDetector()
    samples = []
    for i in range(60):
        # The mouth is still, but the head sweeps across the frame.
        samples.append(still_face(i / FPS, nose=(0.5 + 0.02 * i, 0.4)))
    states = run(det, samples)
    assert states[-1] is MouthState.UNKNOWN
    assert MouthState.SPEAKING not in states


def test_no_face_is_unknown_not_silent():
    """A candidate who has left the frame, or whom the camera cannot see, is not quiet."""
    det = LipActivityDetector()
    samples = [still_face(i / FPS) for i in range(20)]
    samples += [LipSample(t=(20 + i) / FPS, lip_opening=None) for i in range(20)]
    states = run(det, samples)
    assert states[-1] is MouthState.UNKNOWN


def test_a_pause_between_words_does_not_flip_speaking_to_silent():
    """The hold rule: leave SPEAKING only after the mouth has been still for hold_seconds."""
    det = LipActivityDetector(LipThresholds(hold_seconds=0.5))
    speaking = [still_face(i / FPS, lip=0.01 + (0.04 if i % 2 else 0.0)) for i in range(40)]
    short_pause = [still_face(t / FPS) for t in range(40, 46)]  # 0.3 s of stillness
    states = run(det, speaking + short_pause)
    assert states[-1] is MouthState.SPEAKING


def test_a_long_quiet_spell_becomes_silent():
    det = LipActivityDetector(LipThresholds(hold_seconds=0.5))
    speaking = [still_face(i / FPS, lip=0.01 + (0.04 if i % 2 else 0.0)) for i in range(40)]
    quiet = [still_face(t / FPS) for t in range(40, 80)]  # 2 s of stillness
    states = run(det, speaking + quiet)
    assert states[-1] is MouthState.SILENT


def test_the_state_is_unknown_before_any_judgement():
    det = LipActivityDetector()
    assert det.state is MouthState.UNKNOWN
    assert det.push(still_face(0.0)) is MouthState.UNKNOWN  # one frame has no change yet


def test_sample_from_landmarks_normalises_by_face_height():
    class P:
        def __init__(self, x, y):
            self.x, self.y = x, y

    landmarks = [P(0.5, 0.5) for _ in range(153)]
    landmarks[10] = P(0.5, 0.2)   # forehead
    landmarks[152] = P(0.5, 0.7)  # chin: face height 0.5
    landmarks[13] = P(0.5, 0.45)  # upper inner lip
    landmarks[14] = P(0.5, 0.50)  # lower inner lip: opening 0.05
    landmarks[1] = P(0.52, 0.4)   # nose tip

    s = sample_from_landmarks(1.0, landmarks)
    assert math.isclose(s.lip_opening, 0.1, rel_tol=1e-6)
    assert math.isclose(s.face_height, 0.5, rel_tol=1e-6)
    assert s.nose_x == 0.52


def test_sample_from_landmarks_without_a_face_has_no_opening():
    class P:
        def __init__(self):
            self.x = self.y = 0.5

    s = sample_from_landmarks(1.0, [P() for _ in range(153)])
    assert s.lip_opening is None
