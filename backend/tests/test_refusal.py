"""Tests for handling a candidate who declines to answer.

Driven by a real session. The candidate said "No", then "I don't want to." three
more times. The bot invented a strategy they had never described, thanked them
for sharing it, and carried on asking about it.

Two properties are under test:

1. **Detection is conservative.** Treating a real answer as a refusal would make
   the bot abandon a topic the candidate was engaging with, which is worse than
   missing a refusal.
2. **The response is decent.** Change the subject rather than re-ask; after
   enough refusals, close the interview. The bot is not entitled to an answer.
"""

import pytest

from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain
from bot.brain.refusal import is_substantive, looks_like_refusal
from bot.brain.state import BrainConfig
from shared.contracts import CoverageLevel, SectionKind


# -- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "No",
        "no.",
        "Nope",
        "I don't want to",
        "I don't want to.",
        "I'd rather not",
        "I'd rather not say.",
        "No comment.",
        "Pass",
        "skip it",
        "Um, no.",
        "Well, I'd rather not.",
        "Can we move on?",
        "I want to stop.",
    ],
)
def test_clear_refusals_are_detected(text):
    assert looks_like_refusal(text)


@pytest.mark.parametrize(
    "text",
    [
        # The critical class: starts with "no" but is an answer.
        "No, that's not how it went — what we actually did was reroute traffic.",
        "No one on the team agreed with me at first, so I had to build the case.",
        "I don't want to overstate it, but I led the pricing decision myself.",
        "Pass rates were the metric I cared about most.",
        "Skip logic in the onboarding flow was my main project that year.",
        "I'd rather not overclaim here — I contributed but did not own it.",
        "Stop-the-line authority was something I pushed for and eventually got.",
    ],
)
def test_answers_that_merely_start_like_refusals_are_not_refusals(text):
    """Abandoning a topic the candidate is engaging with is the worse error."""
    assert not looks_like_refusal(text)


@pytest.mark.parametrize("text", ["um", "yeah", "right", "ok", "", "   "])
def test_fillers_are_not_substantive_but_are_not_refusals(text):
    assert not is_substantive(text)
    assert not looks_like_refusal(text)


def test_a_real_answer_is_substantive():
    assert is_substantive("I owned the payouts platform for about four years.")


# -- brain behaviour ---------------------------------------------------------


def build(**overrides) -> InterviewBrain:
    config = BrainConfig(floor_turns=2, ceiling_turns=6, **overrides)
    return InterviewBrain(load_blueprint(), config=config)


def refuse(brain: InterviewBrain, times: int, *, seconds: float = 10.0) -> None:
    for _ in range(times):
        brain.tick(brain.elapsed_seconds + seconds)
        brain.observe(bot_text="A question.", candidate_text="I don't want to.")


def finish_opening(brain: InterviewBrain, *, seconds: float = 10.0) -> None:
    """Drive past the opening warm-up, however many turns it takes."""
    while brain.current_section.kind == SectionKind.OPENING:
        brain.tick(brain.elapsed_seconds + seconds)
        brain.observe(
            bot_text="Hello, tell me a bit about yourself.",
            candidate_text="I have about twelve years in product management.",
        )


def answer(brain: InterviewBrain, times: int = 1, *, seconds: float = 10.0) -> None:
    for _ in range(times):
        brain.tick(brain.elapsed_seconds + seconds)
        brain.observe(
            bot_text="A question.",
            candidate_text="I led the payments migration end to end for two years.",
        )


def test_two_refusals_change_the_topic_instead_of_re_asking():
    """Re-asking a question someone has twice declined is neither productive
    nor decent."""
    brain = build()
    finish_opening(brain)
    first_section = brain.current_section.id

    refuse(brain, 2)

    assert brain.current_section.id != first_section


def test_a_refusal_then_an_answer_resets_the_count():
    """One reluctant answer must not doom the rest of the interview."""
    brain = build()
    finish_opening(brain)
    section = brain.current_section.id

    brain.observe(bot_text="q", candidate_text="I'd rather not.")
    answer(brain, 1)

    assert brain.current_section.id == section  # still exploring the topic


def test_persistent_refusal_ends_the_interview_gracefully():
    """A candidate declining everything is disengaged, upset, or done."""
    brain = build()
    answer(brain, 1)

    refuse(brain, 4)

    assert brain.current_section.kind == SectionKind.CLOSING


def test_declining_is_recorded_separately_from_answering_badly():
    """These mean different things about a person, and only one is about
    their ability."""
    brain = build()
    finish_opening(brain)
    topic = brain.current_section.id
    refuse(brain, 2)

    outcome = next(o for o in brain.outcomes() if o.section_id == topic)

    assert outcome.declined_turns == 2
    assert outcome.coverage == CoverageLevel.INSUFFICIENT
    assert outcome.coverage_shortfall is True
    assert "declined" in outcome.shortfall_reason.lower()


def test_opening_is_not_scored_sufficient_when_the_candidate_refused():
    """A live session recorded coverage 'sufficient' for an opening in which
    the candidate said only "No. I don't want to."."""
    brain = build()
    refuse(brain, 3)  # enough to exhaust the opening warm-up

    opening = next(o for o in brain.outcomes() if o.section_id == "opening")
    assert opening.coverage == CoverageLevel.INSUFFICIENT
    assert opening.coverage_shortfall is True


def test_prompt_tells_the_model_to_back_off_after_a_refusal():
    brain = build()
    finish_opening(brain)
    brain.observe(bot_text="q", candidate_text="I don't want to.")

    prompt = " ".join(brain.plan_turn().system_instruction.lower().split())
    assert "declined" in prompt
    assert "do not repeat the question they declined" in prompt


def test_prompt_offers_a_way_out_after_repeated_refusals():
    brain = build()
    finish_opening(brain)
    brain.observe(bot_text="q", candidate_text="I don't want to.")
    brain.observe(bot_text="q", candidate_text="I don't want to.")

    prompt = " ".join(brain.plan_turn().system_instruction.lower().split())
    assert "end the interview here if they would prefer" in prompt
    assert "without pressure" in prompt


def test_prompt_never_claims_history_that_does_not_exist():
    """The exact live failure: an empty window plus "continue naturally" made
    the model invent "you mentioned earlier that you shaped a key product
    strategy" for a candidate who had only refused."""
    brain = build()
    refuse(brain, 3)  # refuses through the opening, into the first topic
    assert brain.current_section.kind == SectionKind.COMPETENCY

    prompt = " ".join(brain.plan_turn().system_instruction.lower().split())
    assert "have not answered anything yet" in prompt
    assert 'do not say "you mentioned earlier"' in prompt
    assert "you have been talking with this candidate for a while" not in prompt


def test_prompt_switches_to_normal_footing_once_they_answer():
    brain = build()
    finish_opening(brain)
    assert brain.current_section.kind == SectionKind.COMPETENCY

    prompt = " ".join(brain.plan_turn().system_instruction.lower().split())
    assert "you have been talking with this candidate for a while" in prompt
    assert "have not answered anything yet" not in prompt
