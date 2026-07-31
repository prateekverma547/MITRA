"""A candidate asking to leave, and the call actually ending.

Two things were wrong. Somebody who said plainly that they wanted to stop was
only listened to after four consecutive refusals, so they got asked several more
questions first. And nothing ever ended the call: `is_finished` existed and only
the text harness read it, so even a normally completed interview sat there after
the goodbye until the candidate hung up.

The detector here is held to a stricter standard than any other in the system.
A missed withdrawal costs one more question, which they can repeat. A false one
ends someone's interview and cannot be undone.
"""

import pytest

from bot.brain.brain import InterviewBrain
from bot.brain.withdrawal import (
    StopIntent,
    asked_explicitly,
    classify,
    confirms_stopping,
    declines_stopping,
    wants_to_stop,
)
from tests.test_brain import tiny_blueprint


# -- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "I don't want to continue the interview",
        "I want to stop",
        "Can we stop here?",
        "I'd like to end the interview",
        "I have to go",
        "I'm done",
    ],
)
def test_asking_to_leave_is_recognised(said):
    assert wants_to_stop(said) is True


@pytest.mark.parametrize(
    "said",
    [
        "I don't want to talk about my last employer",
        "I'd rather not answer that one",
        "I don't want to continue down that line, what actually happened was different",
        "Can we stop talking about the pricing decision?",
        "I'm done with that project now, it shipped last year",
        "I want to stop doing manual QA, that's why I moved",
    ],
)
def test_talking_about_something_else_is_not_leaving(said):
    """The consequence is terminal, so this is the test that matters most.
    "I don't want to talk about my last employer" must never end an interview."""
    assert wants_to_stop(said) is False


def test_confirmations_and_refusals_of_the_offer():
    assert confirms_stopping("yes") is True
    assert confirms_stopping("yes please") is True
    assert declines_stopping("no, let's continue") is True
    assert declines_stopping("carry on") is True
    # A bare yes only means anything once the offer has been made, which the
    # brain enforces rather than the detector.
    assert wants_to_stop("yes") is False


# -- the brain offers, it does not assume ------------------------------------


def brain_for():
    return InterviewBrain(tiny_blueprint())


def test_asking_to_stop_produces_an_offer_not_an_ending():
    """One misdetection should cost a sentence, not an interview."""
    brain = brain_for()
    brain.observe(bot_text="Hello, how are you today?")
    brain.observe(candidate_text="I want to stop")

    instruction = brain.plan_turn().system_instruction

    assert "THEY HAVE ASKED TO STOP" in instruction
    assert "Do not ask why" in instruction
    assert brain.withdrew is False
    assert brain.is_finished is False


def test_confirming_ends_the_interview():
    brain = brain_for()
    brain.observe(bot_text="Hello, how are you today?")
    brain.observe(candidate_text="I want to stop")
    brain.observe(bot_text="Of course. Would you like to end here, or carry on?")
    brain.observe(candidate_text="yes")

    assert brain.withdrew is True
    assert brain.current_section.kind == "closing"

    brain.observe(bot_text="Thank you for your time. Take care.")
    assert brain.is_finished is True


def test_declining_the_offer_carries_on_as_though_it_had_not_come_up():
    """Somebody who changes their mind must not be treated as half-out."""
    brain = brain_for()
    brain.observe(bot_text="Hello, how are you today?")
    brain.observe(candidate_text="can we stop")
    brain.observe(bot_text="Of course. Would you like to end here, or carry on?")
    brain.observe(candidate_text="no, let's continue")

    assert brain.withdrew is False
    assert brain.is_finished is False
    assert "THEY HAVE ASKED TO STOP" not in brain.plan_turn().system_instruction


def test_an_ambiguous_reply_to_the_offer_is_taken_as_carrying_on():
    """Pressing somebody about leaving is worse than one wasted turn."""
    brain = brain_for()
    brain.observe(bot_text="Hello, how are you today?")
    brain.observe(candidate_text="I'm done")
    brain.observe(bot_text="Of course. Would you like to end here, or carry on?")
    brain.observe(candidate_text="Well, I suppose I could talk about the pricing work.")

    assert brain.withdrew is False
    assert "THEY HAVE ASKED TO STOP" not in brain.plan_turn().system_instruction


def test_leaving_outranks_a_repair():
    """Somebody who asked to stop must not then be asked to repeat themselves."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    brain.observe(candidate_text="I want to stop")
    brain.observe(bot_text="Of course. Would you like to end here, or carry on?")
    brain.observe(candidate_text="yes")
    brain.observe(bot_text="Thank you for your time.")

    assert brain.is_finished is True
    assert brain.repairs_requested == 0


# -- the closing is short, and does not fish for more ------------------------


def test_the_goodbye_does_not_invite_more_conversation():
    """They asked to leave. Inviting questions is not respecting that."""
    brain = brain_for()
    brain.observe(bot_text="Hello, how are you today?")
    brain.observe(candidate_text="I have to go")
    brain.observe(bot_text="Of course. Would you like to end here, or carry on?")
    brain.observe(candidate_text="yes please")

    instruction = brain.plan_turn().system_instruction

    assert "BECAUSE THEY ASKED TO STOP" in instruction
    assert "invite any brief" not in instruction
    assert "Do not ask why" in instruction


def test_a_normal_interview_still_gets_the_normal_close():
    brain = brain_for()
    brain._index = len(brain.sections) - 1  # jump to the closing

    instruction = brain.plan_turn().system_instruction

    assert "invite any brief" in instruction
    assert "BECAUSE THEY ASKED TO STOP" not in instruction


# -- what the record says ----------------------------------------------------


def test_withdrawing_is_recorded_as_a_decision_not_a_failure():
    """"Chose to stop" says something about a decision. It says nothing about
    their ability, and the report must not blur the two."""
    brain = brain_for()
    brain.observe(bot_text="Hello, how are you today?")
    brain.observe(candidate_text="I want to stop")
    brain.observe(bot_text="Of course. Would you like to end here, or carry on?")
    brain.observe(candidate_text="yes")

    skipped = [o for o in brain.outcomes() if o.coverage_shortfall]

    assert brain.withdrew is True
    assert skipped, "competencies never reached must be recorded as gaps"
    assert all("chose to end the interview" in o.shortfall_reason for o in skipped)
    # And it says so plainly, so a reader cannot mistake it for a verdict.
    assert all("not their ability" in o.shortfall_reason for o in skipped)
    # Never the time-limit wording, which would blame the clock for a decision.
    assert not any("time limit" in o.shortfall_reason for o in skipped)


# -- caught only by a real conversation --------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Sorry, I do not want to continue the interview.",
        "I'm sorry, I want to stop",
        "Apologies, I have to go",
        "um, sorry, can we stop here",
    ],
)
def test_an_apology_before_asking_to_leave_still_counts(said):
    """People apologise when they withdraw. Almost nobody says "I want to stop"
    flatly.

    The first version missed exactly this. A live run said "Sorry, I do not want
    to continue the interview" and the interviewer replied by asking for
    feedback about the interview process, then carried on questioning. The unit
    tests all passed, because they used the phrases without the apology.
    """
    assert wants_to_stop(said) is True


@pytest.mark.parametrize(
    "said",
    [
        "Sorry, I should explain, we actually shipped two of them.",
        "Sorry about the noise, what I meant was we scaled it back.",
        "Sorry, could you say that again?",
    ],
)
def test_an_apology_before_a_real_answer_is_still_an_answer(said):
    """"Sorry" is not a filler elsewhere and must not become one: it opens
    plenty of genuine answers."""
    assert wants_to_stop(said) is False


# -- verbatim from the session where this failed -----------------------------
#
# Four requests to stop in under a minute. None were detected, and the
# interviewer answered the third by asking a fresh question about learning new
# business domains. The first detector matched whole utterances against a fixed
# phrase list, so it caught "I want to stop" and nothing anybody actually says.

LIVE_TRANSCRIPT = [
    "I don't want to continue this interview. Just end this interview.",
    "Just end in the interview.",
    "In the interview, I don't want to continue the interview",
    "No, just end the interview.",
]


@pytest.mark.parametrize("said", LIVE_TRANSCRIPT)
def test_what_was_actually_said_is_detected(said):
    assert wants_to_stop(said) is True


@pytest.mark.parametrize("said", LIVE_TRANSCRIPT)
def test_what_was_actually_said_is_an_instruction_not_a_hint(said):
    """Being asked "are you sure?" after this is the insult. It was asked twice
    more after the first of these."""
    assert asked_explicitly(said) is True


def test_an_explicit_instruction_ends_it_without_asking_again():
    brain = brain_for()
    brain.observe(bot_text="So, tell me what you're working on at the moment.")
    brain.observe(candidate_text="I don't want to continue this interview. Just end this interview.")

    assert brain.withdrew is True
    assert brain.current_section.kind == "closing"
    # Not an offer. They already told us.
    assert "THEY HAVE ASKED TO STOP" not in brain.plan_turn().system_instruction

    brain.observe(bot_text="Thank you for your time. Take care.")
    assert brain.is_finished is True


def test_the_interviewer_never_asks_a_new_question_after_being_told_to_stop():
    """Live it asked: "Before we wrap up, can you share an example of a time you
    had to quickly learn a new business domain?" """
    brain = brain_for()
    brain.observe(bot_text="Tell me about a project you led.")
    brain.observe(candidate_text="Just end the interview.")

    instruction = brain.plan_turn().system_instruction

    assert brain.current_section.kind == "closing"
    assert "BECAUSE THEY ASKED TO STOP" in instruction
    assert "Do not ask if they have questions" in instruction


def test_repeating_the_request_is_taken_as_a_yes():
    """Somebody who says it again has answered the offer, whatever words they
    used. A third asking is not acceptable."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="I'm done")           # soft, so it offers
    brain.observe(bot_text="Would you like to end here, or carry on?")
    brain.observe(candidate_text="No, just end the interview.")

    assert brain.withdrew is True


# -- and the answers that must survive a looser detector ---------------------


@pytest.mark.parametrize(
    "said",
    [
        "I don't want to talk about my last employer",
        "I don't want to continue down that line, what actually happened was different",
        "I want to stop doing manual QA, that's why I moved",
        "We had to end the project early because of budget",
        "I'm done with that project now, it shipped last year",
        "Sorry, I should explain, we actually built two of them.",
    ],
)
def test_searching_inside_the_utterance_still_does_not_eat_answers(said):
    """Every pattern names the interview, the call or the session. Ending a
    project is not ending an interview."""
    assert classify(said) is StopIntent.NONE
