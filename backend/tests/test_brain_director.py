"""Tests for the Pipecat adapter that drives the brain in the live pipeline.

Deterministic: no network, no real LLM. These guard the two properties that make
the voice wiring correct rather than merely working —

1. **Streaming survives.** The director rewrites context and lets the frame
   through. It must never await a completed response, or time-to-first-audio
   roughly doubles.
2. **Judgement never blocks a spoken turn.** Off-path work is spawned, not
   awaited, even when the judge is slow or throws.
"""

import asyncio
from contextlib import contextmanager

import pytest
from loguru import logger
from pipecat.frames.frames import (
    LLMContextFrame,
    LLMUpdateSettingsFrame,
    TextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain, Judgment
from bot.brain.state import BrainConfig
from bot.brain_director import BrainDirector
from shared.contracts import CoverageLevel, KeyClaim


class StubLLM:
    """Stands in for the OpenAI service; only identity matters to the director."""


def context_with(messages: list[dict]) -> LLMContextFrame:
    context = LLMContext()
    context.set_messages(messages)
    return LLMContextFrame(context=context)


class Conversation:
    """Simulates how the aggregators actually feed the director.

    They append to the *same* context the director rewrote, so each frame
    carries the director's window plus the assistant reply and the new user
    message. Building a fresh one-message context per turn — as an earlier
    version of these tests did — hides bugs in how new messages are identified.
    """

    def __init__(self, director):
        self._director = director
        self._context = LLMContext()

    async def say(self, candidate_text: str, *, bot_text: str = "A question."):
        messages = list(self._context.get_messages())
        messages.append({"role": "assistant", "content": bot_text})
        messages.append({"role": "user", "content": candidate_text})
        self._context.set_messages(messages)
        await send(self._director, LLMContextFrame(context=self._context))


class BareDirector(BrainDirector):
    """Bypasses FrameProcessor plumbing that requires a running pipeline."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pushed = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)

    def get_clock(self):
        return None


def make(judge=None, config=None):
    brain = InterviewBrain(load_blueprint(), config=config or BrainConfig())
    director = BareDirector(brain=brain, llm=StubLLM(), judge=judge, session_id="test")
    return brain, director


async def send(director, frame):
    """Invoke the director's own logic, skipping base-class pipeline setup."""
    from pipecat.processors.frame_processor import FrameProcessor

    original = FrameProcessor.process_frame
    async def noop(self, frame, direction):
        return None
    FrameProcessor.process_frame = noop
    try:
        await BrainDirector.process_frame(director, frame, FrameDirection.DOWNSTREAM)
    finally:
        FrameProcessor.process_frame = original


# -- context rewriting -------------------------------------------------------


async def test_context_is_replaced_with_the_section_window():
    """The growing chat log is gone: what reaches the LLM is the brain's plan."""
    brain, director = make()
    frame = context_with(
        [
            {"role": "assistant", "content": "Tell me about your background."},
            {"role": "user", "content": "Eleven years in product."},
            # Stale history that must not survive.
            {"role": "user", "content": "SHOULD BE DROPPED"},
        ]
    )

    await send(director, frame)

    contents = [m["content"] for m in frame.context.get_messages()]
    assert contents == [m["content"] for m in brain.plan_turn().messages]
    # The context handed to the model is the brain's plan, not the accumulated
    # chat log the aggregators would otherwise grow.
    assert len(contents) <= 3


async def test_system_instruction_is_retargeted_before_the_context_frame():
    """Order matters: the service must have the new instruction before inference."""
    brain, director = make()

    await send(director, context_with([{"role": "user", "content": "Hello."}]))

    kinds = [type(f).__name__ for f in director.pushed]
    assert kinds == ["LLMUpdateSettingsFrame", "LLMContextFrame"]

    settings_frame = director.pushed[0]
    assert isinstance(settings_frame, LLMUpdateSettingsFrame)
    assert settings_frame.service is director._llm
    assert "Senior Product Manager" in settings_frame.delta.system_instruction


async def test_non_context_frames_pass_through_untouched():
    brain, director = make()
    frame = TextFrame(text="unrelated")

    await send(director, frame)

    assert director.pushed == [frame]


async def test_repeated_identical_answers_each_count_as_a_turn():
    """A candidate repeating themselves must still advance the interview.

    An earlier version de-duplicated by text, so "I don't want to." said four
    times counted once, the section never hit its ceiling, and the bot asked
    about the same topic forever. Observed live.
    """
    brain, director = make(config=BrainConfig(floor_turns=1, ceiling_turns=9))
    conversation = Conversation(director)

    for _ in range(4):
        await conversation.say("I don't want to.")

    candidate_turns = [t for t in brain.transcript if t.speaker == "candidate"]
    assert len(candidate_turns) == 4


# -- the judge must never block ---------------------------------------------


class SlowJudge:
    """Takes longer than any turn could afford."""

    def __init__(self):
        self.started = 0

    async def assess(self, request):
        self.started += 1
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled, never awaited")


class ThrowingJudge:
    async def assess(self, request):
        raise RuntimeError("judge exploded")


async def test_slow_judge_does_not_delay_the_turn():
    judge = SlowJudge()
    brain, director = make(judge=judge, config=BrainConfig(floor_turns=1, ceiling_turns=9))

    conversation = Conversation(director)
    # Get into the decision band so a judgement is requested.
    await conversation.say("I led the payments migration end to end.")
    await conversation.say("We cut failure rates by nearly forty percent.")

    started = asyncio.get_running_loop().time()
    await conversation.say("The hardest part was the acquirer contracts.")
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5, "director awaited the judge"
    await director.cleanup()


async def test_judge_failure_does_not_break_the_interview():
    brain, director = make(
        judge=ThrowingJudge(), config=BrainConfig(floor_turns=1, ceiling_turns=9)
    )

    conversation = Conversation(director)
    await conversation.say("I led the payments migration end to end.")
    await conversation.say("We cut failure rates by nearly forty percent.")
    await conversation.say("The hardest part was the acquirer contracts.")
    await asyncio.sleep(0.05)  # let the spawned task run and fail

    # Still planning turns; heuristics simply carry on.
    assert brain.plan_turn().section_id


# -- diagnosability ----------------------------------------------------------


async def test_section_transitions_are_recorded_for_the_session_log():
    brain, director = make(config=BrainConfig(floor_turns=1, ceiling_turns=1))

    conversation = Conversation(director)
    for text in (
        "I led the payments migration end to end.",
        "We cut failure rates by nearly forty percent.",
        "The hardest part was the acquirer contracts.",
    ):
        await conversation.say(text)

    kinds = [e["kind"] for e in director.events]
    assert "section_started" in kinds
    assert "section_ended" in kinds

    ended = next(e for e in director.events if e["kind"] == "section_ended")
    assert {"section", "coverage", "turns", "shortfall"} <= set(ended)


async def test_judgment_results_are_recorded():
    class QuickJudge:
        async def assess(self, request):
            return Judgment(
                section_id=request.section_id,
                coverage=CoverageLevel.SUFFICIENT,
                claims=[
                    KeyClaim(text="a claim", section_id=request.section_id, turn_index=0)
                ],
            )

    brain, director = make(
        judge=QuickJudge(), config=BrainConfig(floor_turns=1, ceiling_turns=9)
    )

    conversation = Conversation(director)
    # The opening is a warm-up; judgements are only requested once a competency
    # section is under way.
    for line in (
        "I have about twelve years in product management.",
        "Mostly payments and marketplace products.",
        "I led the payments migration end to end.",
        "We cut failure rates by nearly forty percent.",
    ):
        await conversation.say(line)
    await asyncio.sleep(0.05)

    judgments = [e for e in director.events if e["kind"] == "judgment"]
    assert judgments
    assert judgments[0]["coverage"] == "sufficient"
    assert judgments[0]["claims"] == 1


# -- a failed judgement must leave a trace -----------------------------------
#
# The fallback to heuristics is correct and is not what these test. What they
# test is that the failure is *visible*. It was not: `assess` swallowed the
# exception, so the director's own warning could never fire, and `_record` only
# ran on success. A dead judge produced an interview that completed normally
# with no claims, no carryover and no contradictions, which reads in the report
# as a candidate who never said anything specific.


@contextmanager
def captured_warnings():
    """Collect what loguru emits, since it does not route through caplog."""
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


class SilentlyFailingJudge:
    """What `OpenAIJudge` actually does: handles its own error, returns None.

    This is the real-world shape of the failure — a bad OPENAI_BLUEPRINT_MODEL,
    an expired key, a timeout. Nothing propagates, so nothing upstream can see
    it unless the None is treated as the failure it is.
    """

    def __init__(self):
        self.calls = 0

    async def assess(self, request):
        self.calls += 1
        return None


async def probe_a_competency(director, lines=4):
    """Talk long enough that the brain asks for a depth judgement."""
    conversation = Conversation(director)
    for line in (
        "I have about twelve years in product management.",
        "Mostly payments and marketplace products.",
        "I led the payments migration end to end.",
        "We cut failure rates by nearly forty percent.",
        "The hardest part was the acquirer contracts.",
        "I would sequence the rollout differently now.",
    )[:lines]:
        await conversation.say(line)
    await asyncio.sleep(0.05)


async def test_a_judge_that_raises_is_logged_and_recorded():
    brain, director = make(
        judge=ThrowingJudge(), config=BrainConfig(floor_turns=1, ceiling_turns=9)
    )

    with captured_warnings() as warnings:
        await probe_a_competency(director)

    failures = [e for e in director.events if e["kind"] == "judgment_failed"]
    assert failures, "a failed judgement left no event"
    assert failures[0]["section"]
    assert failures[0]["request_kind"] in ("depth", "section_end")
    assert failures[0]["error"] == "RuntimeError"

    assert any("judgement failed" in line for line in warnings), (
        "a failed judgement left no log line"
    )


async def test_a_judge_that_returns_nothing_is_recorded():
    """The branch a real failure arrives on, and the one that was silent."""
    judge = SilentlyFailingJudge()
    brain, director = make(judge=judge, config=BrainConfig(floor_turns=1, ceiling_turns=9))

    await probe_a_competency(director)

    assert judge.calls, "no judgement was ever requested, so this proves nothing"
    failures = [e for e in director.events if e["kind"] == "judgment_failed"]
    assert len(failures) == judge.calls
    assert all(f["section"] and f["request_kind"] for f in failures)


async def test_the_interview_transitions_normally_when_every_judgement_fails():
    """The fallback is the point. Visibility must not have cost it."""
    brain, director = make(
        judge=SilentlyFailingJudge(), config=BrainConfig(floor_turns=1, ceiling_turns=2)
    )

    await probe_a_competency(director, lines=6)

    # Heuristics carried the interview: it left the opening, worked through a
    # competency, and is still planning turns.
    started = [e["section"] for e in director.events if e["kind"] == "section_started"]
    assert started, "the interview never left the opening"
    assert brain.plan_turn().section_id
    assert not brain.is_finished


async def test_the_failure_counter_increments_per_failed_attempt():
    judge = SilentlyFailingJudge()
    brain, director = make(judge=judge, config=BrainConfig(floor_turns=1, ceiling_turns=9))

    await probe_a_competency(director, lines=6)

    summary = director.judgment_summary()
    assert summary["judgments_attempted"] == judge.calls
    assert summary["judgments_failed"] == judge.calls
    assert summary["judgments_attempted"] > 1, "only one attempt proves nothing about counting"


class WorkingJudge:
    async def assess(self, request):
        return Judgment(section_id=request.section_id, coverage=CoverageLevel.PARTIAL)


class AlternatingJudge:
    """Succeeds, fails, succeeds... so every counter has to move independently."""

    def __init__(self):
        self.calls = 0

    async def assess(self, request):
        self.calls += 1
        if self.calls % 2:
            return Judgment(section_id=request.section_id, coverage=CoverageLevel.PARTIAL)
        return None


async def test_a_working_judge_records_no_failures():
    brain, director = make(judge=WorkingJudge(), config=BrainConfig(floor_turns=1, ceiling_turns=9))

    await probe_a_competency(director)

    assert not [e for e in director.events if e["kind"] == "judgment_failed"]
    assert director.judgment_summary()["judgments_failed"] == 0
    assert director.judgment_summary()["judgments_attempted"] > 0


async def test_a_working_judge_increments_succeeded_not_failed():
    """Without this counter, a success and a teardown cancellation look alike."""
    brain, director = make(judge=WorkingJudge(), config=BrainConfig(floor_turns=1, ceiling_turns=9))

    await probe_a_competency(director, lines=6)

    summary = director.judgment_summary()
    assert summary["judgments_succeeded"] == summary["judgments_attempted"]
    assert summary["judgments_failed"] == 0


async def test_a_mix_of_working_and_failing_judgements_is_counted_three_ways():
    judge = AlternatingJudge()
    brain, director = make(judge=judge, config=BrainConfig(floor_turns=1, ceiling_turns=9))

    await probe_a_competency(director, lines=6)

    summary = director.judgment_summary()
    assert judge.calls >= 2, "need at least one of each to tell the counters apart"
    assert summary["judgments_attempted"] == judge.calls
    assert summary["judgments_succeeded"] == (judge.calls + 1) // 2
    assert summary["judgments_failed"] == judge.calls // 2
    assert summary["judgments_succeeded"] > 0 and summary["judgments_failed"] > 0


async def test_attempted_equals_succeeded_plus_failed_when_nothing_is_cancelled():
    """The gap between them is cancellation, so with none it must close exactly.

    A drift here means an outcome path that increments `attempted` and then
    records nothing, which is the bug this counter exists to make visible.
    """
    for judge in (WorkingJudge(), SilentlyFailingJudge(), AlternatingJudge(), ThrowingJudge()):
        brain, director = make(judge=judge, config=BrainConfig(floor_turns=1, ceiling_turns=9))

        await probe_a_competency(director, lines=6)

        summary = director.judgment_summary()
        assert summary["judgments_attempted"] > 0, type(judge).__name__
        assert (
            summary["judgments_attempted"]
            == summary["judgments_succeeded"] + summary["judgments_failed"]
        ), f"{type(judge).__name__} left an unaccounted judgement: {summary}"
