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

import pytest
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
