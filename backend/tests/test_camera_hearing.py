"""The camera's part in the hearing check.

No camera, no MediaPipe: landmarks are supplied directly, and frames are synthetic.
The rules protected here: the camera only ever removes an unheard count; only the
candidate's video is read; a failing camera never takes the call down.
"""

import numpy as np
import pytest
from pipecat.frames.frames import UserImageRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from bot.camera_hearing import MAX_ERRORS, CameraMouthObserver, _to_rgb
from bot.hearing import HearingMonitor, HearingThresholds
from bot.lip_activity import MouthState

STOP_SECS = 0.2


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeHearing:
    def __init__(self):
        self.states = []

    def note_mouth(self, state):
        self.states.append(state)


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


def marks(lip_gap, nose=(0.5, 0.4)):
    """153 landmarks for a face 0.5 high with the given inner-lip gap."""
    pts = [Pt(0.5, 0.5) for _ in range(153)]
    pts[10] = Pt(0.5, 0.2)
    pts[152] = Pt(0.5, 0.7)
    pts[13] = Pt(0.5, 0.45)
    pts[14] = Pt(0.5, 0.45 + lip_gap)
    pts[1] = Pt(*nose)
    return pts


def frame(user="candidate", w=8, h=6, fmt="RGB"):
    return UserImageRawFrame(
        user_id=user, image=bytes(w * h * 3), size=(w, h), format=fmt
    )


def pushed(f):
    return FramePushed(source=None, destination=None, frame=f, direction=FrameDirection.DOWNSTREAM, timestamp=0)


def make_observer(sequence, clock, hearing=None):
    """`sequence` is a list of landmark sets (or None for no face), used in order."""
    it = iter(sequence)
    obs = CameraMouthObserver(
        hearing=hearing or FakeHearing(),
        landmarks=lambda rgb: next(it),
        clock=clock,
        sample_hz=5.0,
    )
    return obs


@pytest.mark.asyncio
async def test_only_the_first_video_source_is_read_as_the_candidate():
    clock = FakeClock()
    hearing = FakeHearing()
    obs = make_observer([marks(0.0)] * 10, clock, hearing)

    await obs.on_push_frame(pushed(frame("candidate")))
    clock.now += 1
    await obs.on_push_frame(pushed(frame("someone_else")))

    assert obs._candidate_id == "candidate"
    assert len(hearing.states) == 1  # the second source's frame was ignored


@pytest.mark.asyncio
async def test_frames_are_sampled_at_the_configured_rate():
    clock = FakeClock()
    hearing = FakeHearing()
    obs = make_observer([marks(0.0)] * 200, clock, hearing)

    for _ in range(40):  # 40 frames at 20 fps over 2 seconds
        await obs.on_push_frame(pushed(frame()))
        clock.now += 0.05
    assert 9 <= len(hearing.states) <= 11  # about 5 per second for 2 seconds


@pytest.mark.asyncio
async def test_still_lips_are_reported_as_silent_once_judged():
    clock = FakeClock()
    hearing = FakeHearing()
    obs = make_observer([marks(0.0)] * 40, clock, hearing)
    for _ in range(40):
        await obs.on_push_frame(pushed(frame()))
        clock.now += 0.2
    assert hearing.states[-1] is MouthState.SILENT


@pytest.mark.asyncio
async def test_a_failing_camera_is_switched_off_without_raising():
    clock = FakeClock()

    def broken(rgb):
        raise RuntimeError("model crashed")

    obs = CameraMouthObserver(hearing=FakeHearing(), landmarks=broken, clock=clock, sample_hz=5.0)
    for _ in range(MAX_ERRORS + 5):
        await obs.on_push_frame(pushed(frame()))
        clock.now += 1.0

    assert obs.disabled is True


@pytest.mark.asyncio
async def test_non_video_frames_are_ignored():
    clock = FakeClock()
    hearing = FakeHearing()
    obs = make_observer([marks(0.0)], clock, hearing)
    from pipecat.frames.frames import TextFrame

    await obs.on_push_frame(pushed(TextFrame("hello")))
    assert hearing.states == []


def test_bgr_frames_are_converted_to_rgb():
    img = np.zeros((2, 2, 3), dtype=np.uint8)
    img[..., 0] = 255  # blue channel in BGR order
    f = UserImageRawFrame(user_id="c", image=img.tobytes(), size=(2, 2), format="BGR")
    rgb = _to_rgb(f)
    assert rgb[0, 0, 2] == 255 and rgb[0, 0, 0] == 0


# -- the hearing monitor's side of the rule ---------------------------------------


def hearing_monitor(clock):
    return HearingMonitor(
        session_id="t",
        thresholds=HearingThresholds(transcript_grace_seconds=0.0),
        clock=clock,
    )


async def unheard_utterance(monitor, clock, mouth=None, seconds=1.0):
    await monitor.on_push_frame(pushed(VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=clock.now)))
    clock.now += seconds / 2
    if mouth is not None:
        monitor.note_mouth(mouth)
    clock.now += seconds / 2
    await monitor.on_push_frame(pushed(VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=clock.now)))


@pytest.mark.asyncio
async def test_still_lips_through_an_utterance_mean_it_is_not_counted_as_unheard():
    """Regression intent: noise with no answer behind it must not end the call."""
    clock = FakeClock()
    monitor = hearing_monitor(clock)
    for _ in range(6):
        await unheard_utterance(monitor, clock, mouth=MouthState.SILENT)
    assert monitor._unheard_utterances == 0
    assert any(e["event"] == "not_counted_lips_still" for e in monitor.events)


@pytest.mark.asyncio
async def test_a_moving_mouth_is_counted_as_before():
    clock = FakeClock()
    monitor = hearing_monitor(clock)
    await unheard_utterance(monitor, clock, mouth=MouthState.SPEAKING)
    assert monitor._unheard_utterances == 1


@pytest.mark.asyncio
async def test_no_camera_reading_leaves_the_count_unchanged():
    clock = FakeClock()
    monitor = hearing_monitor(clock)
    await unheard_utterance(monitor, clock, mouth=None)
    assert monitor._unheard_utterances == 1


@pytest.mark.asyncio
async def test_an_unjudged_camera_does_not_suppress_the_count():
    clock = FakeClock()
    monitor = hearing_monitor(clock)
    await unheard_utterance(monitor, clock, mouth=MouthState.UNKNOWN)
    assert monitor._unheard_utterances == 1


@pytest.mark.asyncio
async def test_camera_evidence_from_before_the_utterance_is_not_used():
    clock = FakeClock()
    monitor = hearing_monitor(clock)
    monitor.note_mouth(MouthState.SILENT)  # no utterance in progress: ignored
    await unheard_utterance(monitor, clock, mouth=None)
    assert monitor._unheard_utterances == 1
