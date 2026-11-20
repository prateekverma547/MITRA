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


# -- politeness must not hide a request -------------------------------------
#
# Every one of these is verbatim from a saved live session, and all seven were
# read as answers before "please" was ignored. "Can you please repeat?" has
# been missed in four separate interviews, and the interviewer asked a new
# question each time.


@pytest.mark.parametrize(
    "said",
    [
        "Can you please repeat?",
        "Can you please repeat the question?",
        "Sorry, can you please repeat that?",
        "Could you please repeat the question?",
        "Please repeat that",
        "can you repeat please",
        "Pardon, could you please say that again?",
        "Can you pls repeat?",
    ],
)
def test_a_polite_request_to_repeat_is_still_a_request(said):
    assert classify(said) is RepairKind.REPEAT
    assert is_repair(said) is True


@pytest.mark.parametrize(
    "said",
    ["Could you please rephrase that?", "Can you please be more specific?"],
)
def test_a_polite_request_to_simplify_is_still_a_request(said):
    assert classify(said) is RepairKind.SIMPLIFY


@pytest.mark.parametrize(
    "said",
    [
        # "please" with no request in it is not a request.
        "please",
        # Asking for the candidate's own content, not a repeat of ours.
        "Can you please repeat the deployment steps your team followed?",
        "Please walk me through the release process we used.",
        "Please tell me about a time it went wrong.",
    ],
)
def test_please_alone_or_inside_an_answer_is_not_a_repair(said):
    assert classify(said) is RepairKind.NONE


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
        "Sorry, what we did was build a tool for the finance team.",
        "Apologies, we ran it for two quarters before anyone noticed.",
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

    instruction = brain.plan_turn().system_instruction
    assert "THEY DID NOT HEAR YOU" not in instruction
    assert "LEAVE THAT QUESTION" in instruction
    # The third request is still a repair request, and the health report must
    # see it as one: that count is what separates a poor line from a quiet
    # candidate.
    assert brain.repairs_requested == MAX_REPAIR_ATTEMPTS + 1


def test_a_third_unheard_question_is_not_taken_as_an_answer():
    """Before this, "sorry?" on the third go was passed to the model as what the
    candidate said, and the model followed up on it. The candidate said nothing
    substantive, so nothing substantive may be credited."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    for _ in range(MAX_REPAIR_ATTEMPTS):
        brain.observe(candidate_text="sorry?")
        brain.observe(bot_text="Tell me about a product you shipped.")

    brain.observe(candidate_text="sorry?")

    section = brain.current_section
    assert section.substantive_turns == 0
    assert section.declined_turns == 0
    assert section.turns_spent == 1  # one turn spent on nothing said


def test_the_question_it_moves_on_to_gets_its_own_two_attempts():
    """The fix must not turn one unheard question into a permanent state. The
    counter resets when the interviewer moves on, so the next question can be
    repeated as the first one was."""
    brain = brain_for()
    brain.observe(bot_text="Tell me about a product you shipped.")
    for _ in range(MAX_REPAIR_ATTEMPTS):
        brain.observe(candidate_text="sorry?")
        brain.observe(bot_text="Tell me about a product you shipped.")
    brain.observe(candidate_text="sorry?")  # gives up on the first question

    new_question = "Walk me through how you prioritised the backlog."
    brain.observe(bot_text=new_question)
    brain.observe(candidate_text="sorry?")

    instruction = brain.plan_turn().system_instruction
    assert "THEY DID NOT HEAR YOU" in instruction
    assert new_question in instruction
    assert "Tell me about a product you shipped." not in instruction


def test_a_long_run_of_unheard_questions_never_produces_a_follow_up():
    """The loop from the live sessions, run far past the cap. Every candidate
    turn here is a request to repeat, so no turn may be credited as an answer,
    and the interviewer's question is always the last one it asked."""
    brain = brain_for()
    for n in range(4):
        question = f"Question number {n} about a product you shipped."
        brain.observe(bot_text=question)
        for _ in range(MAX_REPAIR_ATTEMPTS + 1):
            brain.observe(candidate_text="sorry?")
            brain.observe(bot_text=question)

    assert brain.current_section.substantive_turns == 0
    assert brain.current_section.declined_turns == 0


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


# -- "I don't know", however it arrives ---------------------------------------


@pytest.mark.parametrize(
    "said",
    ["dunno", "I dunno.", "Um, I dunno", "I don know", "I dont no", "I do no"],
)
def test_the_spoken_forms_of_not_knowing_are_still_an_answer(said):
    """"I don't know" said at speed is "I dunno", and STT spells it several ways.
    Every one is the same honest answer and must never loop as a repair."""
    assert classify(said) is RepairKind.NONE
    assert is_repair(said) is False


@pytest.mark.parametrize("said", ["I know.", "I know", "i no", "Um, I know."])
def test_a_bare_i_know_is_unclear(said):
    """Live in `int_fb22eafe1628`, eight "I don't know"s were transcribed as
    exactly this."""
    assert classify(said) is RepairKind.UNCLEAR
    assert is_repair(said) is False


@pytest.mark.parametrize(
    "said",
    [
        "I know the codebase well, we built the ingestion layer ourselves.",
        "I know. We measured it weekly against the finance numbers.",
        "I know that one, it was the reconciliation project.",
    ],
)
def test_i_know_inside_an_answer_is_an_answer(said):
    """Whole-utterance matching, as everywhere in this module."""
    assert classify(said) is RepairKind.NONE


@pytest.mark.parametrize(
    ("question", "closed"),
    [
        ("Do you know what UAT is?", True),
        ("Okay. Have you ever mapped a current process?", True),
        ("Thanks. Is that something you owned?", True),
        ("Walk me through how you gathered the requirements.", False),
        ("What trade-off did you make there?", False),
        ("Okay. Do you know it? How did you measure it?", False),
        ("", False),
    ],
)
def test_which_questions_can_be_answered_yes_or_no(question, closed):
    from bot.brain.repair import asks_yes_or_no

    assert asks_yes_or_no(question) is closed


def test_i_know_to_an_open_question_is_checked_not_taken_as_an_answer():
    """The interviewer asks rather than moving on, and the check spends no turn:
    nothing has been answered yet."""
    brain = brain_for()
    asked = "Walk me through how you gathered the requirements."
    brain.observe(bot_text=asked)
    before = brain.current_section.turns_spent

    brain.observe(candidate_text="I know.")

    instruction = brain.plan_turn().system_instruction
    assert "YOU MAY HAVE MISHEARD THEM" in instruction
    assert "Did you say you don't" in instruction
    assert "Do not move on" in instruction
    assert brain.current_section.turns_spent == before
    assert brain.unclear_replies == 1


def test_what_we_heard_stays_in_the_transcript():
    """Never rewritten to "I don't know": that would put words in a candidate's
    mouth inside the hiring record."""
    brain = brain_for()
    brain.observe(bot_text="Walk me through how you gathered the requirements.")
    brain.observe(candidate_text="I know.")

    said = [t.text for t in brain.transcript if t.speaker == "candidate"]
    assert said == ["I know."]


def test_an_unclear_reply_is_not_a_request_to_repeat():
    """The candidate asked for nothing. Counting it as a repair would read in the
    report as them struggling to hear, and would spend the two attempts a real
    "sorry, what?" is entitled to."""
    brain = brain_for()
    asked = "Walk me through how you gathered the requirements."
    brain.observe(bot_text=asked)
    brain.observe(candidate_text="I know.")
    brain.observe(bot_text="Sorry, I didn't quite catch that. Did you say you don't know?")
    brain.observe(candidate_text="Sorry, could you say that again?")

    assert brain.repairs_requested == 1
    assert "THEY DID NOT HEAR YOU" in brain.plan_turn().system_instruction


def test_i_know_to_a_yes_or_no_question_is_an_answer():
    """After "Do you know what UAT is?", "I know." is a perfectly good reply."""
    brain = brain_for()
    brain.observe(bot_text="Do you know what user acceptance testing is?")

    brain.observe(candidate_text="I know.")

    assert "MISHEARD" not in brain.plan_turn().system_instruction
    assert brain.current_section.turns_spent == 1
    assert brain.unclear_replies == 0


def test_the_check_is_made_once_per_question():
    """If "I know." comes back after the check, it is taken as said. A second
    check is the interrogation the repair cap exists to prevent."""
    brain = brain_for()
    brain.observe(bot_text="Walk me through how you gathered the requirements.")
    brain.observe(candidate_text="I know.")
    brain.observe(bot_text="Sorry, I didn't quite catch that. Did you say you don't know?")

    brain.observe(candidate_text="I know.")

    assert "MISHEARD" not in brain.plan_turn().system_instruction
    assert brain.current_section.turns_spent == 1
    assert brain.unclear_replies == 1


CHECK = "Sorry, I didn't quite catch that. Did you say you don't know?"


def checked(brain, question="Walk me through how you gathered the requirements."):
    """Ask an open question, hear "I know.", and make the one check."""
    brain.observe(bot_text=question)
    brain.observe(candidate_text="I know.")
    brain.observe(bot_text=CHECK)


def test_the_confirmation_is_the_answer():
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text="Yes, I don't know.")

    instruction = brain.plan_turn().system_instruction
    assert "MISHEARD" not in instruction
    assert "THEIR ANSWER WAS \"I DON'T KNOW\"" in instruction
    assert brain.current_section.turns_spent == 1


@pytest.mark.parametrize(
    "said",
    ["Yes.", "Yeah.", "Yes, I said I don't know.", "That's right.", "Um, yes.", "Correct."],
)
def test_a_bare_yes_to_the_check_means_i_dont_know(said):
    """Live in `int_8e3db77a60a9` "Yes." went to the model as a bare answer. It
    said "Sorry, my mistake" and asked the same question again, a dozen times."""
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text=said)

    instruction = brain.plan_turn().system_instruction
    assert "THEIR ANSWER WAS \"I DON'T KNOW\"" in instruction
    assert "Do not ask that question again" in instruction
    assert "MISHEARD" not in instruction
    assert brain.current_section.turns_spent == 1
    # What we heard stays in the record, untouched.
    said_in_record = [t.text for t in brain.transcript if t.speaker == "candidate"]
    assert said_in_record == ["I know.", said]


@pytest.mark.parametrize(
    "said",
    ["No, I said I know how we did it.", "Yes, we used interviews and workshops.", "No."],
)
def test_anything_else_after_the_check_is_taken_as_said(said):
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text=said)

    assert "I DON'T KNOW\" ===" not in brain.plan_turn().system_instruction


def test_the_note_lasts_one_turn():
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text="Yes.")
    assert "I DON'T KNOW\" ===" in brain.plan_turn().system_instruction

    brain.observe(bot_text="No problem. What trade-off did you make on that project?")
    brain.observe(candidate_text="We chose speed over coverage.")

    assert "I DON'T KNOW\" ===" not in brain.plan_turn().system_instruction


def test_once_confirmed_i_know_is_not_checked_again():
    """They have told us what "I know." means. Checking every time is the loop:
    question, "I know", check, "Yes", question again."""
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text="Yes.")

    brain.observe(bot_text="No problem. What trade-off did you make on that project?")
    brain.observe(candidate_text="I know.")

    instruction = brain.plan_turn().system_instruction
    assert "MISHEARD" not in instruction
    assert "THEIR ANSWER WAS \"I DON'T KNOW\"" in instruction
    assert brain.current_section.turns_spent == 2
    assert brain.unclear_replies == 2


def test_without_a_confirmation_the_next_question_gets_its_own_check():
    """No "yes" means we learned nothing, so the next "I know." is checked."""
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text="I don't know.")

    brain.observe(bot_text="What trade-off did you make on that project?")
    brain.observe(candidate_text="I know.")

    assert "MISHEARD" in brain.plan_turn().system_instruction
    assert brain.unclear_replies == 2


def test_a_confirmed_i_know_to_a_yes_or_no_question_is_still_an_answer():
    brain = brain_for()
    checked(brain)
    brain.observe(candidate_text="Yes.")

    brain.observe(bot_text="Do you know what user acceptance testing is?")
    brain.observe(candidate_text="I know.")

    assert "I DON'T KNOW\" ===" not in brain.plan_turn().system_instruction


def test_the_loop_from_int_8e3db77a60a9_does_not_recur():
    """The live sequence, replayed: each open question answered "I know.", and
    the first check confirmed. Only one check is ever made."""
    brain = brain_for()
    questions = [
        "What criteria did you use to split the budget?",
        "What was the bet behind that strategy?",
        "What attribution models have you used?",
        "What went wrong, and how did you fix it?",
    ]
    checks = 0
    for question in questions:
        brain.observe(bot_text=question)
        brain.observe(candidate_text="I know.")
        if "MISHEARD" in brain.plan_turn().system_instruction:
            checks += 1
            brain.observe(bot_text=CHECK)
            brain.observe(candidate_text="Yes.")
        assert "THEIR ANSWER WAS \"I DON'T KNOW\"" in brain.plan_turn().system_instruction

    assert checks == 1
