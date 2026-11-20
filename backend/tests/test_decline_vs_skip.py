"""Decline versus skip, round 4 (A-46, A-53). Decided behaviour:
  - a decline ("I'd rather not answer", "I prefer not to discuss that") gets an
    easier question on the same topic, and is recorded as a decline;
  - a skip ("can we skip this", "next question", "move on", and "pass") gets the
    next plan question, and is recorded as a skip;
  - "I don't know" is an answer: neither.
The model's behaviour was measured by replays (A_checks/decline_runs.py); these
tests hold the deterministic parts: the prompt's rules and what is recorded."""

import json
from pathlib import Path

import pytest

from bot.brain.refusal import declined_this_question
from bot.brain.withdrawal import skipped_this_question
from bot.realtime_coordinator import RealtimeCoordinator
from bot.realtime_prompt import build_base_instruction
from shared.contracts import InterviewBlueprint

FIXTURE = Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json"

DECLINES = ["I'd rather not answer that.", "I prefer not to discuss that.", "I'd prefer not to talk about that one.",
            "I'm not comfortable answering that one.", "I don't want to answer that question.", "No comment."]
SKIPS = ["Can we skip this one?", "Next question, please.", "Can we move on from this one?", "Let's move on.",
         "Skip.", "Skip, please.", "Pass.", "I'll pass.", "I'll pass on this one.", "Can I pass on this one?",
         "I don't know this one, can we skip it?"]
NEITHER = ["I don't know.", "I passed on that offer because the role was too narrow.",
           "We decided to skip that release, and it cost us a quarter.", "I want to end the interview.",
           "I'd rather skip the rest of this interview."]


@pytest.mark.parametrize("text", DECLINES)
def test_a_decline_is_recorded_as_a_decline_not_a_skip(text):
    assert declined_this_question(text) and not skipped_this_question(text)


@pytest.mark.parametrize("text", SKIPS)
def test_a_skip_or_a_pass_is_recorded_as_a_skip_not_a_decline(text):
    assert skipped_this_question(text) and not declined_this_question(text)


@pytest.mark.parametrize("text", NEITHER)
def test_an_answer_or_a_stop_request_is_neither(text):
    assert not skipped_this_question(text) and not declined_this_question(text)


class Spy:
    def __getattr__(self, name):
        async def call(*a, **k):
            return None
        return call


async def test_the_coordinator_records_each_as_its_own_kind():
    bp = InterviewBlueprint.model_validate(json.loads(FIXTURE.read_text()))
    coord = RealtimeCoordinator(blueprint=bp, llm=Spy())
    await coord._on_candidate_turn("I prefer not to discuss that.")
    await coord._on_candidate_turn("I'll pass on this one.")
    kinds = [e["kind"] for e in coord.events if e["kind"] in ("question_declined", "question_skipped")]
    assert kinds == ["question_declined", "question_skipped"]


def base():
    bp = InterviewBlueprint.model_validate(json.loads(FIXTURE.read_text()))
    return build_base_instruction(bp, first_name="Asha", time_of_day="afternoon")


def block(text, header):
    """One rule block: from its header line to the next blank-line-and-capitals header."""
    import re

    start = text.index(f"\n{header}\n") + 1
    nxt = re.search(r"\n\n[A-Z][A-Z ]+\n", text[start + len(header):])
    return text[start: start + len(header) + nxt.start()] if nxt else text[start:]


def test_the_prompt_sends_a_decline_to_an_easier_question_not_the_next_plan_question():
    text = base()
    decline = " ".join(block(text, "WHEN THEY DECLINE A QUESTION").split())  # round 14: reflowed
    assert "easier question about the same thing they declined" in decline
    assert "It is NOT the next numbered question" in decline
    # Round 5: no worked example. Mitra copied the old one's frame ("What makes a call
    # like that hard for you?") on PM plans; the rule describes "easier" instead.
    assert "What makes a call like that hard" not in decline and "Say this:" not in decline
    assert "general terms" in decline and "smaller or more recent instance" in decline
    assert "never ask why they declined" in decline, "round 5: one reply asked what made the topic sensitive"
    assert "I prefer not to discuss that" in decline
    # The cause of A-46: the plan rule allowed only plan questions and follow-ups.
    assert "after they decline a question, one easier question about the same thing" in text
    assert "there is no third kind" not in text


def test_the_prompt_sends_a_skip_or_a_pass_to_the_next_plan_question():
    skip = " ".join(block(base(), "WHEN THEY ASK TO SKIP A QUESTION").split())  # round 14: reflowed
    assert "ask the next numbered question in the current area" in skip
    assert "not even an easier version of it" in skip
    assert "I'll pass on this one" in skip
    assert "easier question" not in skip
