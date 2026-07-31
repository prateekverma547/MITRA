"""Hanging up when the interview is over.

The first version of this put the check in `BrainDirector`, a processor sitting
between the user aggregator and the LLM. `BotStoppedSpeakingFrame` is emitted by
`transport.output()`, several positions further down, so the director never saw
it. Live, the candidate asked to stop, the bot said goodbye, and the call stayed
open with both of them sitting in it.

Every unit test passed, because they all drove the brain directly and never sent
a frame through a pipeline. So the central test here builds a real one and emits
the frame from the **last** processor, which is the only shape that would have
failed.
"""

import asyncio

import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, EndWorkerFrame, TextFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)

from bot.ending import SessionEnder


class FakeBrain:
    def __init__(self, finished=True, withdrew=False):
        self.is_finished = finished
        self.withdrew = withdrew


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)

    def ended(self):
        return [f for f in self.frames if isinstance(f, EndWorkerFrame)]


def pushed(frame, source=None):
    return FramePushed(
        source=source, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )


def ender_with(brain, worker=None):
    ender = SessionEnder(brain=brain, session_id="t")
    ender.attach(worker or FakeWorker())
    return ender


# -- the shape that failed live ----------------------------------------------


class TailEmitter(FrameProcessor):
    """Stands in for transport.output(): emits from the END of the pipeline.

    This is the whole point. A processor placed before it, which is where the
    check used to live, never sees what it emits.
    """

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, TextFrame):
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)


async def test_the_observer_sees_a_frame_emitted_by_the_last_processor():
    """The bug, in one test.

    Runs a real pipeline through Pipecat's own harness. The frame that has to
    trigger the ending is emitted by the processor at the end, which is why the
    check cannot live in a processor before it.
    """
    from pipecat.tests.utils import run_test

    worker = FakeWorker()
    ender = ender_with(FakeBrain(finished=True, withdrew=True), worker)

    # What matters is that the observer saw it, not what the sink collected,
    # so no expectation is placed on frame flow here.
    await run_test(
        TailEmitter(name="tail"),
        frames_to_send=[TextFrame("Thanks for your time. Goodbye.")],
        observers=[ender],
    )

    assert ender.ended is True, (
        "the observer never saw the frame, which is exactly what happened live"
    )
    assert worker.ended()[0].reason == "candidate_withdrew"


# -- when it fires -----------------------------------------------------------


async def test_it_ends_once_the_interview_is_finished():
    worker = FakeWorker()
    ender = ender_with(FakeBrain(finished=True), worker)

    await ender.on_push_frame(pushed(BotStoppedSpeakingFrame()))

    assert worker.ended()[0].reason == "interview_complete"


async def test_a_withdrawal_is_named_as_one():
    """The record should say they chose to leave, not that the interview
    happened to finish."""
    worker = FakeWorker()
    ender = ender_with(FakeBrain(finished=True, withdrew=True), worker)

    await ender.on_push_frame(pushed(BotStoppedSpeakingFrame()))

    assert worker.ended()[0].reason == "candidate_withdrew"


async def test_it_does_not_end_mid_interview():
    worker = FakeWorker()
    ender = ender_with(FakeBrain(finished=False), worker)

    await ender.on_push_frame(pushed(BotStoppedSpeakingFrame()))

    assert worker.ended() == []
    assert ender.ended is False


async def test_it_waits_for_the_goodbye_to_finish():
    """Ends on the bot having *stopped* speaking. Ending any earlier would cut
    off the goodbye."""
    worker = FakeWorker()
    ender = ender_with(FakeBrain(finished=True), worker)

    await ender.on_push_frame(pushed(TextFrame("Thanks for your time.")))

    assert worker.ended() == []


async def test_it_ends_only_once():
    """The same frame is pushed by more than one processor."""
    worker = FakeWorker()
    ender = ender_with(FakeBrain(finished=True), worker)
    frame = BotStoppedSpeakingFrame()

    await ender.on_push_frame(pushed(frame))
    await ender.on_push_frame(pushed(frame))
    await ender.on_push_frame(pushed(BotStoppedSpeakingFrame()))

    assert len(worker.ended()) == 1


# -- degrading rather than breaking ------------------------------------------


async def test_no_brain_means_no_ending():
    """The Milestone 1 baseline path runs without a brain and must still work."""
    worker = FakeWorker()
    ender = SessionEnder(brain=None, session_id="t")
    ender.attach(worker)

    await ender.on_push_frame(pushed(BotStoppedSpeakingFrame()))

    assert worker.ended() == []


async def test_a_missing_worker_is_logged_not_raised():
    """Losing the hang-up is bad. Crashing the bot at the end of a real
    interview, after the transcript matters, is worse."""
    ender = SessionEnder(brain=FakeBrain(finished=True), session_id="t")

    await ender.on_push_frame(pushed(BotStoppedSpeakingFrame()))

    assert ender.ended is True


# -- wiring ------------------------------------------------------------------


def test_the_ender_is_registered_as_an_observer_not_a_pipeline_processor():
    """The original bug was a placement mistake, so placement is asserted.

    A processor cannot see frames emitted downstream of it, which is why this
    has to be an observer and why putting it back in the pipeline would silently
    stop the call ever ending.
    """
    import inspect

    from bot import run_bot

    source = inspect.getsource(run_bot)
    assert "observers=[transcript_observer, latency_observer, silence, ender]" in source
    assert "ender.attach(worker)" in source
    # And it is not in the pipeline processor list.
    pipeline_block = source[source.index("pipeline = Pipeline("):source.index("worker = PipelineWorker(")]
    assert "ender" not in pipeline_block
