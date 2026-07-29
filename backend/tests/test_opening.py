"""Tests for the opening warm-up.

Driven by candidate feedback on a live session: the interview "starts banging".
The first thing heard was a single long breath containing a greeting, the role,
and a three-part question about a named employer.

Two separable problems, both tested here:

1. **Structure** — hello, orientation and connection are three turns, not one.
2. **Pacing** — long sentences are spoken as one rushed breath regardless of
   the TTS speed setting, so sentence length is a prompt-level rule.
"""

import pytest

from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain
from bot.brain.state import BrainConfig
from shared.contracts import SectionKind


def build(*, time_of_day: str | None = "evening", candidate: str | None = None):
    blueprint = load_blueprint()
    if candidate:
        blueprint = blueprint.model_copy(
            update={"candidate_name": candidate, "candidate_summary": "Twelve years in AI product."}
        )
    return InterviewBrain(blueprint, config=BrainConfig(), time_of_day=time_of_day)


def prompt_of(brain) -> str:
    return " ".join(brain.plan_turn().system_instruction.lower().split())


def say(brain, text="I'm doing well, thanks for asking."):
    brain.tick(brain.elapsed_seconds + 10)
    brain.observe(bot_text="Hello there.", candidate_text=text)


# -- the first thing they hear ----------------------------------------------


def test_first_turn_greets_and_introduces_itself():
    """A candidate about to be interviewed by a machine must be told so in the
    first breath, not left to ask "who are you?" — which is what happened live."""
    prompt = prompt_of(build())

    assert "say hello and introduce yourself" in prompt
    assert "you are an ai interviewer" in prompt
    assert "do not skip this" in prompt
    assert "do not start the interview" in prompt
    assert "absolutely not yet: the role details" in prompt


def test_first_turn_uses_the_time_of_day():
    assert "good evening" in prompt_of(build(time_of_day="evening"))
    assert "good morning" in prompt_of(build(time_of_day="morning"))


def test_greeting_falls_back_when_the_time_is_unknown():
    """The brain is pure — if nobody injected a clock it must not guess."""
    prompt = prompt_of(build(time_of_day=None))

    assert "hello" in prompt
    assert "good none" not in prompt


def test_greeting_uses_the_candidates_first_name():
    prompt = prompt_of(build(candidate="Prateek Verma"))

    assert "prateek" in prompt
    # First name only — surnames in a spoken greeting sound like a summons.
    assert "prateek verma." not in prompt


# -- the shape of the warm-up ------------------------------------------------


def test_second_turn_orients_without_diving_in():
    brain = build()
    say(brain)

    prompt = prompt_of(brain)
    assert "orient them" in prompt
    assert "senior product manager" in prompt
    assert "do not mention anything from their cv yet" in prompt


def test_third_turn_allows_one_light_personal_connection():
    """What the candidate asked for: connect a little before going deep."""
    brain = build(candidate="Prateek Verma")
    say(brain)
    say(brain, "I'm currently leading AI product work at an agency.")

    prompt = prompt_of(brain)
    assert "one light connection" in prompt
    assert "small talk, not an interview question" in prompt
    # The leak this closes: turn three asked "how do you decide which features
    # to prioritise" — a competency question wearing a warm-up's clothes.
    assert "do not ask how they decide, prioritise" in prompt


def test_warm_up_is_over_before_the_real_questions():
    brain = build()
    for _ in range(4):
        say(brain, "I have about twelve years in product management.")

    assert brain.current_section.kind == SectionKind.COMPETENCY


def test_opening_carries_no_probing_instructions():
    """The regression: a multi-part question about a named employer, cold.

    The blueprint's interviewing guidance ("probe trade-offs", "push past
    frameworks") and the employer's red flags both steer toward evaluation.
    Neither belongs in a greeting — they were pulling the model back to the
    deep end the warm-up exists to hold off.
    """
    for turns in range(3):
        brain = build(candidate="Prateek Verma")
        for _ in range(turns):
            say(brain, "I have about twelve years in product management.")
        if brain.current_section.kind != SectionKind.OPENING:
            continue

        prompt = prompt_of(brain)
        assert "how to interview" not in prompt
        assert "things worth noticing" not in prompt
        assert "probe trade-offs" not in prompt


def test_red_flags_do_reach_the_competency_sections():
    """They are gathered from the employer, so they must actually be used."""
    brain = build()
    for _ in range(4):
        say(brain, "I have about twelve years in product management.")
    assert brain.current_section.kind == SectionKind.COMPETENCY

    prompt = prompt_of(brain)
    assert "things worth noticing" in prompt
    assert "never deliver a verdict" in prompt


# -- pacing ------------------------------------------------------------------


def test_voice_rules_cap_sentence_length():
    """TTS speed alone cannot fix a long sentence — it is still one breath."""
    prompt = prompt_of(build())

    assert "keep every sentence short" in prompt
    assert "one idea per sentence" in prompt
    assert "full stops, not commas" in prompt


def test_one_question_per_turn_is_stated_checkably():
    """"Ask one question at a time" was being violated routinely. A countable
    rule — exactly one question mark — is harder to slide past."""
    prompt = prompt_of(build())

    assert "exactly one question mark" in prompt


def test_voice_rules_forbid_stacking_greeting_and_question():
    prompt = prompt_of(build())

    assert "never stack a greeting, an explanation and a question" in prompt


def test_tts_speed_is_below_default():
    """An interviewer who rattles makes a candidate feel hurried."""
    from bot.services.tts import TTS_SPEED

    assert 0.7 <= TTS_SPEED < 1.0
