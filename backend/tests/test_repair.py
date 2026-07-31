"""Three things a candidate means when a question misfires, and three answers.

Every string in the "caught from a real run" section below is verbatim from a
scripted conversation that the system previously scored as a clean recording
while the candidate said four separate times that they could not hear.
"""

import pytest

from bot.brain.brain import MAX_REPAIR_ATTEMPTS, InterviewBrain
from bot.brain.repair import RepairKind, classify, is_repair
from bot.brain.state import BrainConfig
from tests.test_brain import tiny_blueprint


# -- the strings that started this -------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Sorry, could you say that again?",
        "Sorry, you cut out there.",
        "Can you repeat the question?",
        "Sorry, I did not catch that.",
    ],
)
def test_caught_from_a_real_run(said):
    """These four went entirely unnoticed before this module existed."""
    assert classify(said) is RepairKind.REPEAT


# -- the three meanings ------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    ["sorry?", "pardon", "come again", "you cut out", "I can't hear you", "um, sorry?"],
)
def test_not_heard_asks_for_the_same_words(said):
    assert classify(said) is RepairKind.REPEAT


@pytest.mark.parametrize(
    "said",
    [
        "what do you mean",
        "I don't understand the question",
        "can you rephrase that",
        "can you be more specific",
        "I don't follow",
    ],
)
def test_not_understood_asks_for_different_words(said):
    """Repeating the same sentence to someone who heard it fine is useless."""
    assert classify(said) is RepairKind.SIMPLIFY


@pytest.mark.parametrize(
    "said",
    ["I don't know", "I'm not sure", "no idea", "I can't remember"],
)
def test_not_knowing_is_an_answer_not_a_repair(said):
    """It is not confusion and it is not a refusal. It is an honest answer to a
    question they cannot answer, and looping on it would punish candour."""
    assert classify(said) is RepairKind.NONE
    assert is_repair(said) is False


# -- it must not eat real answers --------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Sorry, I should explain, we actually built two of them.",
        "What do you mean by that? Well, we scoped it down to one team first.",
        "I'm not sure, but I'd guess about thirty percent.",
        "we shipped a retrieval assistant for support agents",
        "Pardon the jargon, we called it a discovery sprint.",
    ],
)
def test_an_answer_that_starts_like_a_repair_is_still_an_answer(said):
    """Whole-utterance matching is the entire safety property. Reading these as
    repairs would throw the answer away and re-ask what they just addressed."""
    assert classify(said) is RepairKind.NONE


# -- what the brain does with it ---------------------------------------------


def brain_for():
    return InterviewBrain(
        tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4)
    )


def test_a_repair_does_not_spend_a_turn():
    """Asking to repeat carries no answer. Counting it would let the depth ramp
    advance on nothing and spend the section's budget on silence."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    before = brain.current_section.turns_spent

    brain.observe(candidate_text="Sorry, could you say that again?")

    assert brain.current_section.turns_spent == before
    assert brain.repairs_requested == 1


def test_the_same_question_comes_back():
    """A live run showed the interviewer answer a repair by abandoning the
    question and asking a different one, losing the answer and its coverage."""
    brain = brain_for()
    asked = "Tell me about a product strategy you personally set."
    brain.observe(bot_text=asked)
    brain.observe(candidate_text="Sorry, you cut out there.")

    instruction = brain.plan_turn().system_instruction

    assert "THEY DID NOT HEAR YOU" in instruction
    assert asked in instruction
    assert "Do not ask anything" in instruction


def test_a_confused_candidate_gets_different_words():
    brain = brain_for()
    brain.observe(bot_text="How did you approach prioritisation?")
    brain.observe(candidate_text="what do you mean")

    instruction = brain.plan_turn().system_instruction

    assert "DID NOT FOLLOW" in instruction
    assert "Do not repeat it word for word" in instruction


def test_a_real_answer_clears_the_repair():
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    brain.observe(candidate_text="sorry?")
    brain.observe(bot_text="Tell me about a product you shipped.")
    brain.observe(candidate_text="We shipped a retrieval assistant for support agents last year.")

    assert "THEY DID NOT HEAR YOU" not in brain.plan_turn().system_instruction
    assert brain.current_section.turns_spent == 1


def test_the_interview_moves_on_after_two_attempts():
    """A third go at the same question is an interrogation. Recording that the
    ground was never covered is more honest than grinding at someone."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    for _ in range(MAX_REPAIR_ATTEMPTS):
        brain.observe(candidate_text="sorry?")
        brain.observe(bot_text="Tell me about a product you shipped.")

    brain.observe(candidate_text="sorry?")

    assert "THEY DID NOT HEAR YOU" not in brain.plan_turn().system_instruction
    assert brain.repairs_requested == MAX_REPAIR_ATTEMPTS


def test_repairs_are_reported_for_the_feedback_report():
    """The count is what tells a reader the recording was poor rather than the
    candidate incoherent."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    brain.observe(candidate_text="Can you repeat the question?")

    from feedback.health import assess
    from shared.contracts import Speaker, Transcript, TranscriptTurn

    transcript = Transcript(
        interview_id="int_1",
        turns=[
            TranscriptTurn(index=0, speaker=Speaker.INTERVIEWER, text="q", at_seconds=0.0),
            TranscriptTurn(index=1, speaker=Speaker.CANDIDATE, text="a b c d", at_seconds=5.0),
        ],
        duration_seconds=10.0,
    )
    health = assess(transcript, {}, repair_requests=brain.repairs_requested + 3)

    assert health.repair_requests == 4
    assert health.degraded is True
