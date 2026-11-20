"""Speaking over the interviewer must not cost the candidate the question.

When the candidate cuts in, Pipecat stops the audio and keeps only the words that
were spoken. Nothing noticed: the half-question went into the record as asked,
"hmm" or "sorry, one second" was taken as its answer, a turn was spent, and the
interviewer moved on. Measured against gpt-4.1-mini before the fix: after "Hmm."
or "Sorry, one second." over a cut-off question it asked a new question six times
out of six.

Deterministic: drives the brain the way the live pipeline does, with
`bot_was_interrupted()` arriving before the turn is observed.
"""

import pytest

from bot.brain.brain import MAX_REPAIR_ATTEMPTS, InterviewBrain
from bot.brain.repair import RepairKind, classify_interjection
from bot.brain.state import BrainConfig
from tests.test_brain import tiny_blueprint

CUT = "Right. What metric did you use to judge whether the"


def brain_for():
    return InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4))


def mid_section():
    """A brain in a competency, one answered question in."""
    brain = brain_for()
    for _ in range(10):
        if brain.current_section.kind.value == "competency":
            break
        brain.observe(bot_text="How are you today?")
        brain.observe(candidate_text="Good thanks, I lead the payments team at the moment.")
    brain.observe(bot_text="Tell me about the hardest decision you made last year?")
    brain.observe(candidate_text="We moved to a second acquirer to cut failed payments by a third.")
    return brain


def spoken_over(brain, said, cut=CUT):
    """The live order: the event fires, then the turn is observed."""
    brain.bot_was_interrupted(cut)
    brain.observe(bot_text=cut, candidate_text=said)


def instruction(brain):
    return " ".join(brain.plan_turn().system_instruction.split())


# -- the failure --------------------------------------------------------------


@pytest.mark.parametrize("said", ["Hmm.", "Sorry, go on.", "Yeah.", "Okay.", "Sorry?", "Go ahead."])
def test_an_interjection_does_not_spend_a_turn(said):
    brain = mid_section()
    before = brain.current_section.turns_spent

    spoken_over(brain, said)

    assert brain.current_section.turns_spent == before


def test_the_cut_off_question_is_asked_again_in_full():
    brain = mid_section()
    spoken_over(brain, "Hmm.")

    text = instruction(brain)

    assert "YOU WERE CUT OFF" in text
    assert CUT in text
    assert "Do not ask anything new" in text
    assert brain.interruptions_recovered == 1


def test_sorry_what_after_a_cut_off_gets_the_full_question_not_the_fragment():
    """Without this an ordinary repeat would say the half-sentence back verbatim."""
    brain = mid_section()
    spoken_over(brain, "Sorry, what?")

    text = instruction(brain)

    assert "YOU WERE CUT OFF" in text
    assert "THEY DID NOT HEAR YOU" not in text


def test_cut_off_before_the_question_started_still_gets_a_question():
    brain = mid_section()
    spoken_over(brain, "Hmm.", cut="Okay.")

    assert "YOU WERE CUT OFF" in instruction(brain)


# -- asking for a moment ------------------------------------------------------


def test_asking_for_a_moment_gets_a_wait_not_a_question():
    brain = mid_section()
    before = brain.current_section.turns_spent
    spoken_over(brain, "Sorry, one second.")

    text = instruction(brain)

    assert "THEY ASKED YOU TO WAIT" in text
    assert "take your time" in text.lower()
    assert brain.current_section.turns_spent == before


def test_after_the_wait_the_question_comes_back_in_full():
    brain = mid_section()
    spoken_over(brain, "Sorry, one second.")
    brain.observe(bot_text="Of course, take your time.", candidate_text="Okay, I'm back.")

    text = instruction(brain)

    assert "YOU WERE CUT OFF" in text
    assert CUT in text


# -- what must stay an answer --------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Yeah so the risk team pushed back hard on the new limits.",
        "Conversion, mostly, but tied back to revenue per merchant.",
        "I don't know.",
    ],
)
def test_starting_to_answer_over_the_question_is_an_answer(said):
    """Throwing a real answer away is the worse error."""
    brain = mid_section()
    before = brain.current_section.turns_spent

    spoken_over(brain, said)

    assert brain.current_section.turns_spent == before + 1
    assert "YOU WERE CUT OFF" not in instruction(brain)


def test_no_interruption_means_no_change():
    """A plain "hmm" after a question that was heard in full is not this path."""
    brain = mid_section()
    brain.observe(bot_text="What metric did you use?", candidate_text="Hmm.")

    assert "YOU WERE CUT OFF" not in instruction(brain)


def test_it_cannot_loop():
    """Two attempts, like every other repair. A third is an interrogation."""
    brain = mid_section()
    for _ in range(MAX_REPAIR_ATTEMPTS):
        spoken_over(brain, "Hmm.")
    before = brain.current_section.turns_spent

    spoken_over(brain, "Hmm.")

    assert brain.current_section.turns_spent == before + 1


def test_a_real_answer_resets_the_allowance():
    brain = mid_section()
    spoken_over(brain, "Hmm.")
    brain.observe(bot_text="Sorry, as I was saying, what metric did you use?",
                  candidate_text="Failed payment rate, tied to recovered revenue.")
    before = brain.current_section.turns_spent

    spoken_over(brain, "Hmm.")

    assert brain.current_section.turns_spent == before


def test_ignored_once_the_candidate_is_leaving():
    brain = mid_section()
    brain.candidate_asked_to_stop(said="I want to end the interview")

    brain.bot_was_interrupted(CUT)

    assert brain._interrupted_question is None


# -- the classifier -----------------------------------------------------------


@pytest.mark.parametrize(
    "said, kind",
    [
        ("Hmm.", RepairKind.INTERRUPTED),
        ("Um, sorry, go ahead.", RepairKind.INTERRUPTED),
        ("Sorry, can you repeat?", RepairKind.INTERRUPTED),
        ("", RepairKind.INTERRUPTED),
        ("Hold on.", RepairKind.HOLD),
        ("Sorry, give me a second.", RepairKind.HOLD),
        ("Wait.", RepairKind.HOLD),
        ("Sorry, I should explain, we actually built two of them.", RepairKind.NONE),
        ("Yes, we used Jira for the backlog.", RepairKind.NONE),
        ("No idea.", RepairKind.NONE),
    ],
)
def test_classify_interjection(said, kind):
    assert classify_interjection(said) is kind


def test_the_bot_tells_the_brain_when_it_is_spoken_over():
    import inspect

    from bot import run_bot

    source = inspect.getsource(run_bot)
    assert '@assistant_aggregator.event_handler("on_assistant_turn_stopped")' in source
    assert "brain.bot_was_interrupted(" in source
    assert '"interruptions_recovered"' in source
