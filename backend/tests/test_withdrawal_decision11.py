"""Decision 11 (docs/DEV_PLAN.md:85) on the PIPELINE engine (the fallback), with the
confirmation spoken by code.

Round 14: the realtime engine no longer detects a withdrawal or speaks a confirmation
in code; Mitra asks and ends with end_interview (tests/test_one_voice.py). Its half of
this file was deleted with that code.

    Candidate says                          Result
    any request to stop, no offer pending   code speaks CONFIRMATION_QUESTION, word for word
    yes (any clear yes) to the offer        graceful end: goodbye, then finish
    a second request to stop                graceful end
    no, or carries on answering             offer cleared; the interview continues
    skip this question / let's skip that    not a withdrawal: another question

Pipeline: the REAL BrainDirector and the REAL
end_interview handler from bot/tools.py, over a real InterviewBrain.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.observers.base_observer import FramePushed

from bot.blueprint_source import load_blueprint
from bot.brain.withdrawal import CONFIRMATION_QUESTION
from bot.tools import END_INTERVIEW, register
from tests.test_brain_director import Conversation, make
from tests.test_tools import FakeLLM, FakeParams

STOP_REQUESTS = ["can we stop here", "I want to end the interview"]
SKIPS = ["skip this question", "let's skip that one"]


async def calls(c, said, explicit=True):
    box = {}

    async def cb(result, properties=None):
        box["result"], box["properties"] = result, properties

    await c._handle_end_interview(SimpleNamespace(
        arguments={"said": said, "explicit": explicit}, result_callback=cb))
    return box


async def offered(text="I want to end the interview"):
    c, llm = coordinator()
    await hears(c, text)
    box = await calls(c, text)
    return c, llm, box


def spoken(director):
    return [f.text for f in director.pushed if isinstance(f, TTSSpeakFrame)]


def ran_llm(director):
    return any(isinstance(f, LLMContextFrame) for f in director.pushed)


@pytest.mark.parametrize("said", ["can we stop here", "I want to end the interview"])
async def test_pipeline_a_request_gets_the_confirmation_spoken_by_code(said):
    brain, director = make()
    talk = Conversation(director)
    await talk.say("I lead payments at a fintech.")
    director.pushed.clear()
    await talk.say(said)
    assert spoken(director) == [CONFIRMATION_QUESTION]
    assert not ran_llm(director), "the model does not speak this turn"
    assert brain.withdrew is False


async def test_pipeline_a_request_only_the_model_catches_is_spoken_by_code_too():
    brain = make()[0]
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="Can we wrap this up?")  # the patterns miss it
    llm = FakeLLM()
    register(llm, brain)
    params = FakeParams(said="Can we wrap this up?", explicit=False)
    await llm.registered[END_INTERVIEW](params)
    assert params.spoken() == [CONFIRMATION_QUESTION]
    assert params.properties.run_llm is False


@pytest.mark.parametrize("answer", ["yes", "I said I want to end the interview"])
async def test_pipeline_yes_or_a_second_request_ends_gracefully(answer):
    brain, director = make()
    talk = Conversation(director)
    await talk.say("I lead payments at a fintech.")
    await talk.say("I want to end the interview")
    director.pushed.clear()
    await talk.say(answer, bot_text=CONFIRMATION_QUESTION)
    assert brain.withdrew is True
    assert brain.current_section.kind == "closing"
    assert ran_llm(director), "the model speaks the goodbye from the closing prompt"
    assert spoken(director) == []


@pytest.mark.parametrize("answer", ["no, let's continue", "Well, the pricing work was the hard part."])
async def test_pipeline_no_or_carrying_on_clears_the_offer(answer):
    brain, director = make()
    talk = Conversation(director)
    await talk.say("I lead payments at a fintech.")
    await talk.say("can we stop here")
    await talk.say(answer, bot_text=CONFIRMATION_QUESTION)
    assert brain.withdrew is False and brain._stop_offered is False
    director.pushed.clear()
    await talk.say("can we stop here")  # a later request is confirmed again
    assert spoken(director) == [CONFIRMATION_QUESTION]


async def test_pipeline_a_tool_call_on_the_turn_that_said_no_does_not_end():
    brain = make()[0]
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="can we stop here")
    assert brain.take_confirmation_due() is True
    brain.observe(bot_text=CONFIRMATION_QUESTION)
    brain.observe(candidate_text="no, let's continue")
    llm = FakeLLM()
    register(llm, brain)
    params = FakeParams(said="no, let's continue", explicit=False)
    await llm.registered[END_INTERVIEW](params)
    assert brain.withdrew is False and params.result["ended"] is False
    assert params.spoken() == []


@pytest.mark.parametrize("said", SKIPS)
async def test_pipeline_a_skip_is_not_a_withdrawal(said):
    brain = make()[0]
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text=said)
    llm = FakeLLM()
    register(llm, brain)
    params = FakeParams(said=said, explicit=True)
    await llm.registered[END_INTERVIEW](params)
    assert brain.withdrew is False and brain._stop_offered is False
    assert "ask a different question" in params.result["next"]
    assert params.spoken() == []


