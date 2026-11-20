"""Noticing that the interviewer cannot hear the candidate.

Session `int_3e4f8bb5b499`: Deepgram's socket stalled, every reconnect failed, and the
bot carried on regardless. The candidate talked, nothing was transcribed, the silence
ladder told them to take their time, and the interviewer asked a new question without
having heard the answer. These tests drive the monitor with the frames that session
produced: VAD reporting speech, no transcript, and an STT error.

Deterministic: a fake clock and no transcript grace, so nothing waits on real time.
"""

import asyncio

import pytest
from pipecat.frames.frames import (
    EndWorkerFrame,
    ErrorFrame,
    InterimTranscriptionFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.stt_service import STTService

from bot.hearing import HearingMonitor, HearingThresholds

STOP_SECS = 0.2


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)

    def of(self, kind):
        return [f for f in self.frames if isinstance(f, kind)]


class FakeSTT(STTService):
    async def run_stt(self, audio):
        yield None


def pushed(frame, source=None):
    return FramePushed(
        source=source, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )


class Rig:
    def __init__(self, grace=0, **thresholds):
        self.clock = FakeClock()
        self.worker = FakeWorker()
        self.reasons = []
        self.monitor = HearingMonitor(
            session_id="t",
            thresholds=HearingThresholds(transcript_grace_seconds=grace, **thresholds),
            on_lost=self.reasons.append,
            clock=self.clock,
        )
        self.monitor.attach(self.worker)

    async def feed(self, frame, source=None):
        await self.monitor.on_push_frame(pushed(frame, source))

    async def speak(self, seconds, words=None, words_after_stop=False):
        """The candidate speaks for `seconds`; the STT returns `words`, or nothing.

        `words_after_stop` delivers them after the VAD reports silence, as Deepgram's
        final transcript does (~0.3s later), instead of while they are still talking.
        """
        await self.feed(VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=self.clock.now))
        self.clock.now += seconds + STOP_SECS
        transcript = TranscriptionFrame(text=words or "", user_id="c", timestamp="t")
        if words is not None and not words_after_stop:
            await self.feed(transcript)
        await self.feed(VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=self.clock.now))
        if words is not None and words_after_stop:
            await asyncio.sleep(0.01)
            await self.feed(transcript)
        await self.monitor.settle()

    async def stt_error(self, source=None):
        await self.feed(
            ErrorFrame(error="keepalive ping timeout", processor=source or FakeSTT()),
        )


# -- the session that failed --------------------------------------------------


async def test_a_dead_stt_is_noticed_and_the_call_ends_honestly():
    """Speech keeps arriving, words never do: the candidate is told it is our fault."""
    rig = Rig()
    for _ in range(4):
        await rig.speak(4.0)

    assert rig.monitor.lost is True
    (speak,) = rig.worker.of(TTSSpeakFrame)
    assert "technical problem on my side" in speak.text
    assert "not a reflection on you" in speak.text
    (end,) = rig.worker.of(EndWorkerFrame)
    assert end.reason == "speech_recognition_failed"


async def test_the_session_is_recorded_as_a_technical_failure_not_a_silent_candidate():
    rig = Rig()
    for _ in range(4):
        await rig.speak(4.0)

    (reason,) = rig.reasons
    assert "Speech recognition stopped working" in reason
    assert "not evidence about the candidate" in reason
    assert rig.monitor.summary()["hearing_lost"] is True


async def test_the_silence_ladder_is_stopped_before_the_explanation():
    """Otherwise a nudge can land on top of it."""
    rig = Rig()
    for _ in range(4):
        await rig.speak(4.0)

    kinds = [type(f) for f in rig.worker.frames]
    assert kinds == [UserIdleTimeoutUpdateFrame, TTSSpeakFrame, EndWorkerFrame]
    assert rig.worker.frames[0].timeout == 0


async def test_an_stt_error_needs_less_unheard_speech():
    """The error is half the evidence; two unheard utterances complete it."""
    rig = Rig()
    await rig.stt_error()
    await rig.speak(3.0)
    assert rig.monitor.lost is False
    await rig.speak(3.0)
    assert rig.monitor.lost is True
    assert "after a speech-to-text error" in rig.reasons[0]


# -- what must not end a working interview ------------------------------------


async def test_words_coming_back_means_hearing_is_fine():
    rig = Rig()
    for _ in range(10):
        await rig.speak(6.0, words="We migrated the billing service to the new platform.")
    assert rig.monitor.lost is False
    assert rig.worker.frames == []


async def test_words_that_arrive_just_after_the_speech_still_count():
    """The usual case on Deepgram: the final transcript lands after the VAD stop.
    The grace window exists so that this is not mistaken for silence."""
    rig = Rig(grace=0.2)
    for _ in range(6):
        await rig.speak(4.0, words="We ran UAT with the finance team.", words_after_stop=True)
    assert rig.monitor.lost is False
    assert rig.worker.frames == []


async def test_one_heard_answer_resets_the_count():
    rig = Rig()
    for _ in range(3):
        await rig.speak(4.0)
    await rig.speak(4.0, words="Sorry, yes, I can hear you now.")
    for _ in range(3):
        await rig.speak(4.0)
    assert rig.monitor.lost is False


async def test_an_interim_transcript_counts_as_being_heard():
    rig = Rig()
    for _ in range(3):
        await rig.speak(4.0)
    await rig.feed(InterimTranscriptionFrame(text="so the", user_id="c", timestamp="t"))
    await rig.speak(4.0)
    assert rig.monitor.lost is False


async def test_coughs_and_murmurs_do_not_add_up_to_deafness():
    """Many utterances, but too little speech in them to be sure."""
    rig = Rig()
    for _ in range(8):
        await rig.speak(1.0)
    assert rig.monitor.lost is False


async def test_an_empty_transcript_is_not_being_heard():
    rig = Rig()
    for _ in range(4):
        await rig.speak(4.0, words="   ")
    assert rig.monitor.lost is True


async def test_a_reconnect_that_works_is_given_its_chance():
    rig = Rig()
    await rig.stt_error()
    await rig.speak(3.0)
    await rig.speak(3.0, words="As I was saying, the rollout took two sprints.")
    await rig.speak(3.0)
    assert rig.monitor.lost is False
    assert [e["event"] for e in rig.monitor.events] == ["stt_error", "stt_recovered"]


async def test_an_error_from_another_service_is_not_an_stt_error():
    """A TTS or LLM hiccup says nothing about whether the candidate is heard."""
    rig = Rig()
    await rig.feed(ErrorFrame(error="tts timeout", processor=FrameProcessor(name="tts")))
    await rig.speak(3.0)
    await rig.speak(3.0)
    assert rig.monitor.lost is False
    assert rig.monitor.summary()["stt_errors"] == 0


async def test_it_ends_only_once():
    rig = Rig()
    for _ in range(8):
        await rig.speak(4.0)
    assert len(rig.worker.of(EndWorkerFrame)) == 1
    assert len(rig.reasons) == 1


async def test_a_missing_worker_is_logged_not_raised():
    monitor = HearingMonitor(thresholds=HearingThresholds(transcript_grace_seconds=0))
    clock = 0.0
    for _ in range(4):
        await monitor.on_push_frame(pushed(VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=clock)))
        clock += 5
        await monitor.on_push_frame(pushed(VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=clock)))
    # Real clock here, so the durations come from time.monotonic and are tiny:
    # the point is only that nothing raises.
    await monitor.settle()


# -- placement ----------------------------------------------------------------


async def test_it_survives_registration_in_a_real_pipeline():
    """Pipecat keeps observers in a set; an unhashable observer kills the call."""
    from pipecat.tests.utils import run_test

    rig = Rig()

    class Passthrough(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    await run_test(
        Passthrough(name="p"),
        frames_to_send=[TextFrame("hello")],
        observers=[rig.monitor],
    )
    assert rig.monitor.lost is False


def test_it_is_registered_and_attached_in_the_bot():
    import inspect

    from bot import run_bot

    source = inspect.getsource(run_bot)
    observers = source[source.index("observers=["):]
    observers = observers[: observers.index("]") + 1]
    assert "hearing" in observers
    assert "hearing.attach(worker)" in source
    assert "**hearing.summary()" in source
