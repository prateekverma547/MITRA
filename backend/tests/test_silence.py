"""Tests for the proactive silence ladder.

The safety property under test is negative and therefore easy to lose in a
refactor: a nudge must never fire during a mid-answer thinking pause. That is
enforced by Pipecat's `UserIdleController` (the timer only arms on
`BotStoppedSpeakingFrame` while no user turn is in progress), so what we test
here is our half — the escalation sequence, the reset, and the timeouts we hand
back to that controller.
"""

import pytest
from pipecat.frames.frames import (
    EndWorkerFrame,
    LLMMessagesAppendFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
    UserStartedSpeakingFrame,
)

from bot.silence import SilenceEscalation, SilenceLadder


class FakeWorker:
    """Captures frames the ladder injects."""

    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)

    def of_type(self, frame_type):
        return [f for f in self.frames if isinstance(f, frame_type)]

    @property
    def timeouts(self):
        return [f.timeout for f in self.of_type(UserIdleTimeoutUpdateFrame)]


class _Pushed:
    def __init__(self, frame):
        self.frame = frame


def build(ladder: SilenceLadder | None = None):
    escalation = SilenceEscalation(ladder=ladder, session_id="test")
    worker = FakeWorker()
    escalation.attach(worker)
    return escalation, worker


def test_gaps_are_cumulative_thresholds_converted_to_waits():
    ladder = SilenceLadder(
        nudge_at_seconds=20, audio_check_at_seconds=40, close_at_seconds=120
    )
    # Thresholds are cumulative dead air; the idle controller needs the wait
    # remaining before each rung.
    assert ladder.gaps() == [20, 20, 80]


async def test_stage_one_speaks_a_verbatim_nudge():
    """Stage 1 must not need an LLM round trip — it should land promptly."""
    escalation, worker = build()

    await escalation.handle_idle(aggregator=None)

    spoken = worker.of_type(TTSSpeakFrame)
    assert len(spoken) == 1
    assert spoken[0].text == "Take your time, there's no rush."
    assert worker.of_type(LLMMessagesAppendFrame) == []


async def test_stage_two_rephrases_via_llm_and_mentions_microphone():
    escalation, worker = build()

    await escalation.handle_idle(aggregator=None)
    await escalation.handle_idle(aggregator=None)

    appended = worker.of_type(LLMMessagesAppendFrame)
    assert len(appended) == 1
    instruction = appended[0].messages[0]["content"]
    assert "microphone" in instruction
    assert appended[0].run_llm is True


async def test_stage_three_closes_gracefully_then_ends_the_session():
    escalation, worker = build()

    for _ in range(3):
        await escalation.handle_idle(aggregator=None)

    # The goodbye must be queued before the end frame, or it never gets spoken.
    kinds = [type(f).__name__ for f in worker.frames]
    assert kinds.index("TTSSpeakFrame") < kinds.index("EndWorkerFrame")

    end = worker.of_type(EndWorkerFrame)
    assert len(end) == 1
    assert end[0].reason == "abandoned_by_candidate"
    # The ladder disarms itself so a late timer cannot fire mid-goodbye.
    assert worker.timeouts == [20, 80, 0]


async def test_timeouts_handed_back_match_the_remaining_gaps():
    escalation, worker = build(
        SilenceLadder(nudge_at_seconds=20, audio_check_at_seconds=40, close_at_seconds=120)
    )

    await escalation.handle_idle(aggregator=None)
    assert worker.timeouts == [20]  # wait to reach the 40s rung

    await escalation.handle_idle(aggregator=None)
    assert worker.timeouts == [20, 80]  # wait to reach the 120s rung


async def test_candidate_speaking_resets_the_ladder():
    """A pending escalation is abandoned the instant the candidate speaks."""
    escalation, worker = build()

    await escalation.handle_idle(aggregator=None)  # stage 1 fired
    await escalation.on_push_frame(_Pushed(UserStartedSpeakingFrame()))

    # Ladder is back at the bottom, and the first-rung timeout is restored.
    assert escalation._stage == 0
    assert worker.timeouts[-1] == escalation.initial_timeout

    # The next silence starts again from the gentle nudge, not from stage 2.
    worker.frames.clear()
    await escalation.handle_idle(aggregator=None)
    assert worker.of_type(TTSSpeakFrame)[0].text == "Take your time, there's no rush."


async def test_speaking_before_any_escalation_does_nothing():
    """Normal conversation must not generate silence events."""
    escalation, worker = build()

    await escalation.on_push_frame(_Pushed(UserStartedSpeakingFrame()))

    assert escalation.events == []
    assert worker.frames == []


async def test_events_are_recorded_for_the_session_log():
    escalation, worker = build()

    await escalation.handle_idle(aggregator=None)
    await escalation.on_push_frame(_Pushed(UserStartedSpeakingFrame()))
    await escalation.handle_idle(aggregator=None)

    actions = [e["action"] for e in escalation.events]
    assert actions == ["nudge", "candidate_resumed", "nudge"]
    # Dead air is reported against the threshold that triggered the rung.
    assert escalation.events[0]["dead_air_seconds"] == 20.0


async def test_timings_are_configurable():
    escalation, worker = build(
        SilenceLadder(
            nudge_at_seconds=5,
            audio_check_at_seconds=9,
            close_at_seconds=30,
            nudge_text="Still with me?",
        )
    )

    assert escalation.initial_timeout == 5
    await escalation.handle_idle(aggregator=None)
    assert worker.of_type(TTSSpeakFrame)[0].text == "Still with me?"
    assert worker.timeouts == [4]


async def test_unattached_ladder_warns_instead_of_crashing(caplog):
    """A wiring mistake must not take down a live interview."""
    escalation = SilenceEscalation(session_id="test")  # attach() never called

    await escalation.handle_idle(aggregator=None)  # must not raise

    assert escalation.events[0]["action"] == "nudge"
