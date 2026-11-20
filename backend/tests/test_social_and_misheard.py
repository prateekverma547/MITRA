"""Things a candidate says that are not answers, and the exit that was not asked for.

From `int_39845b4313d4`. Each of these was read as an answer, used up a turn,
and got a brand new question:

    "What are you saying?"  "What are we saying?"  "The last question?"
    "Hello?"                "How are you?"

And twice the interviewer's `end_interview` tool fired on a candidate who was
not leaving: on "But I didn't say it." (correcting a word speech recognition
invented) and on "I don't" (an answer cut short). Both times they were asked
whether they wanted to end the interview.

Deterministic. Whether the model follows the new blocks is checked separately
against the real model.
"""

import pytest

from bot.brain.brain import MAX_REPAIR_ATTEMPTS, InterviewBrain
from bot.brain.repair import RepairKind, classify, classify_interjection
from bot.brain.state import BrainConfig
from bot.brain.withdrawal import could_mean_leaving
from bot.tools import END_INTERVIEW, register
from tests.test_brain import tiny_blueprint
from tests.test_tools import FakeLLM, FakeParams

QUESTION = "What metric did you use to judge whether the migration succeeded?"


def mid_section():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4))
    for _ in range(10):
        if brain.current_section.kind.value == "competency":
            break
        brain.observe(bot_text="How are you today?")
        brain.observe(candidate_text="Good thanks, I lead the payments team at the moment.")
    brain.observe(bot_text="Tell me about the hardest decision you made last year?")
    brain.observe(candidate_text="We moved to a second acquirer to cut failed payments by a third.")
    brain.observe(bot_text=QUESTION)
    return brain


def instruction(brain):
    return " ".join(brain.plan_turn().system_instruction.split())


# -- the classifier -----------------------------------------------------------


@pytest.mark.parametrize(
    "said, kind",
    [
        ("What are you saying?", RepairKind.REPEAT),
        ("What are we saying?", RepairKind.REPEAT),
        ("The last question?", RepairKind.REPEAT),
        ("Sorry, what was the question?", RepairKind.REPEAT),
        ("Hello?", RepairKind.PRESENCE),
        ("Hello.", RepairKind.PRESENCE),
        ("Hello, are you there?", RepairKind.PRESENCE),
        ("Can you hear me?", RepairKind.PRESENCE),
        ("How are you?", RepairKind.SMALL_TALK),
        ("And how are you?", RepairKind.SMALL_TALK),
        # Answers, and must stay answers.
        ("Hello, so the first thing we did was audit the ledger.", RepairKind.NONE),
        ("Good, how are you?", RepairKind.NONE),
        ("I don't know.", RepairKind.NONE),
        ("What we said to the client was that the date would slip.", RepairKind.NONE),
    ],
)
def test_classify(said, kind):
    assert classify(said) is kind


@pytest.mark.parametrize("said", ["Hello?", "How are you?", "What are you saying?"])
def test_over_a_cut_off_question_they_get_the_full_question(said):
    assert classify_interjection(said) is RepairKind.INTERRUPTED


# -- the brain ------------------------------------------------------------------


@pytest.mark.parametrize(
    "said, block",
    [
        ("Hello?", "THEY ARE CHECKING YOU CAN HEAR THEM"),
        ("How are you?", "THEY ASKED HOW YOU ARE"),
        ("What are you saying?", "THEY DID NOT HEAR YOU"),
        ("The last question?", "THEY DID NOT HEAR YOU"),
    ],
)
def test_none_of_these_spends_a_turn_and_the_question_comes_back(said, block):
    brain = mid_section()
    before = brain.current_section.turns_spent

    brain.observe(candidate_text=said)

    assert brain.current_section.turns_spent == before
    text = instruction(brain)
    assert block in text
    assert QUESTION in text


def test_how_are_you_is_answered_as_a_person_not_with_a_job_title():
    brain = mid_section()
    brain.observe(candidate_text="How are you?")

    text = instruction(brain)

    assert "I'm well, thank you." in text
    assert "Do not describe yourself or your role" in text


def test_small_talk_is_not_counted_as_a_connection_problem():
    """`repairs_requested` feeds the report's channel section. "How are you?"
    says nothing about the line."""
    brain = mid_section()
    brain.observe(candidate_text="How are you?")

    assert brain.repairs_requested == 0


def test_hello_is_counted_as_a_connection_problem():
    brain = mid_section()
    brain.observe(candidate_text="Hello?")

    assert brain.repairs_requested == 1


def test_small_talk_past_the_cap_is_taken_as_said_not_as_unheard():
    brain = mid_section()
    for _ in range(MAX_REPAIR_ATTEMPTS):
        brain.observe(candidate_text="How are you?")
        brain.observe(bot_text="I'm well, thank you. " + QUESTION)

    brain.observe(candidate_text="How are you?")

    assert "LEAVE THAT QUESTION" not in instruction(brain)


def test_a_repeat_after_a_nudge_repeats_the_question_not_the_nudge():
    """`int_f1b33f1c0472` and `int_39845b4313d4`: "Can you please repeat?"
    right after "Take your time, there's no rush." found the nudge, and the
    interviewer asked a different question."""
    brain = mid_section()
    brain.observe(bot_text="Take your time, there's no rush.")

    brain.observe(candidate_text="Can you please repeat?")

    text = instruction(brain)
    assert QUESTION in text
    assert '"Take your time' not in text


def test_hello_after_a_nudge_gets_the_question_not_the_nudge():
    brain = mid_section()
    brain.observe(bot_text="Take your time, there's no rush.")

    brain.observe(candidate_text="Hello?")

    assert QUESTION in instruction(brain)


# -- the exit nobody asked for -------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        # Every phrasing in scripts/tool_call_check.py that must end it.
        "Can we wrap this up?",
        "I'd like to call it a day",
        "This isn't for me, sorry",
        "I'm not comfortable doing this anymore",
        "I need to jump off",
        "Actually I'm no longer interested",
        "Something has come up, I need to go",
        "I don't think I want to do this",
        "Bas, ab band karo",
        "I don't want to continue this interview. Just end this interview.",
        "I think I'm done",
        "Can we reschedule?",
        "I've changed my mind",
    ],
)
def test_every_way_of_leaving_still_reaches_the_question(said):
    assert could_mean_leaving(said) is True


@pytest.mark.parametrize(
    "said", ["But I didn't say it.", "I don't", "I know.", "Hello?", "What are you saying?"]
)
def test_the_live_false_alarms_cannot_be_about_leaving(said):
    assert could_mean_leaving(said) is False


@pytest.mark.parametrize("said", ["But I didn't say it.", "I don't"])
async def test_the_tool_on_a_correction_does_not_offer_the_exit(said):
    brain = mid_section()
    brain.observe(candidate_text=said)
    llm = FakeLLM()
    register(llm, brain)

    params = FakeParams(said=said, explicit=True)
    await llm.registered[END_INTERVIEW](params)

    assert params.result["ended"] is False
    assert "Do not ask whether they want to stop" in params.result["next"]
    assert "THEY HAVE ASKED TO STOP" not in brain.plan_turn().system_instruction
    assert brain.withdrew is False


async def test_a_real_soft_withdrawal_is_still_asked_about():
    said = "This isn't for me, sorry"
    brain = mid_section()
    brain.observe(candidate_text=said)
    llm = FakeLLM()
    register(llm, brain)

    await llm.registered[END_INTERVIEW](FakeParams(said=said, explicit=True))

    assert "THEY HAVE ASKED TO STOP" in brain.plan_turn().system_instruction


# -- acknowledging ----------------------------------------------------------------


def test_the_interviewer_acknowledges_from_a_closed_list():
    """`int_39845b4313d4`: answers, "I don't know" and corrections were followed
    straight by the next question. The list is closed so the praise ban stays
    structural: nothing on it says anything about the answer."""
    from bot.brain.prompting import VOICE_RULES

    rules = " ".join(VOICE_RULES.split())
    assert 'Acknowledge with only these' in rules
    for phrase in ('"Thanks."', '"Got it."', '"No problem."', '"Sorry, my mistake."'):
        assert phrase in rules
    assert "NEVER SAY AN ANSWER WAS GOOD" in rules


def test_the_interviewer_no_longer_says_their_words_back():
    """Saying their words back is how "You said automation dots" reached a
    candidate who had not said it."""
    from bot.brain.prompting import VOICE_RULES

    rules = " ".join(VOICE_RULES.split())
    assert "repeat back ONLY words they actually used" not in rules
    assert "Never echo their answer" in rules
