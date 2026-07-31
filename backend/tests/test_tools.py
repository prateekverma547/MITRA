"""The interviewer ending the interview itself.

Pattern matching for "I want to stop" has now failed live twice and been
rewritten once. Even rewritten it misses every one of sixteen natural phrasings,
because a phrase list contains only what somebody thought of in advance. The
model reading those sentences understands all of them; it had no way to act, so
its understanding stayed trapped in prose we then tried to pattern-match back
out of it.

These cover the wiring. Whether the model *chooses* to call the tool is a
behaviour question, and `scripts/withdrawal_run.py` is where that is checked
against real conversations.
"""

import pytest

from bot.brain.brain import InterviewBrain
from bot.tools import END_INTERVIEW, TOOLS, register
from tests.test_brain import tiny_blueprint


class FakeLLM:
    def __init__(self):
        self.registered = {}

    def register_function(self, name, handler, **kwargs):
        self.registered[name] = handler


class FakeParams:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.result = "not called"

    async def result_callback(self, value):
        self.result = value


# -- the schema the model is given -------------------------------------------


def test_the_tool_is_advertised_to_the_model():
    names = [t.name for t in TOOLS.standard_tools]

    assert names == [END_INTERVIEW]


def test_the_description_teaches_the_distinction_that_matters():
    """Leaving the interview, versus talking about ending something else. That
    confusion is the only expensive mistake available here."""
    schema = TOOLS.standard_tools[0]

    assert "any wording and in any language" in schema.description
    assert "end that project early" in schema.description
    assert "Declining a question is not leaving" in schema.description
    assert "do not call it" in schema.description


def test_it_asks_why_so_the_record_says_so():
    schema = TOOLS.standard_tools[0]

    assert set(schema.required) == {"said", "explicit"}


# -- what happens when it is called ------------------------------------------


async def test_a_call_the_patterns_corroborate_ends_the_interview():
    brain = InterviewBrain(tiny_blueprint())
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="Just end this interview.")
    llm = FakeLLM()
    register(llm, brain)

    await llm.registered[END_INTERVIEW](FakeParams(said="end the interview", explicit=True))

    assert brain.withdrew is True
    assert brain.current_section.kind == "closing"


@pytest.mark.parametrize(
    "said",
    [
        "We had to end that project early because of budget",
        "I want to stop doing manual QA, that's why I moved",
    ],
)
async def test_a_call_the_patterns_do_not_corroborate_asks_first(said):
    """Measured false positives from gpt-4.1-mini. Ending an interview because
    somebody described ending a project is the one expensive mistake here, so
    the model alone is not enough to do it."""
    brain = InterviewBrain(tiny_blueprint())
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text=said)
    llm = FakeLLM()
    register(llm, brain)

    await llm.registered[END_INTERVIEW](FakeParams(said=said, explicit=True))

    assert brain.withdrew is False
    assert "THEY HAVE ASKED TO STOP" in brain.plan_turn().system_instruction


async def test_a_phrasing_only_the_model_catches_still_gets_asked():
    """"Can we wrap this up?" is invisible to the patterns, so it is a question
    rather than an ending. One sentence is the cost of being wrong."""
    brain = InterviewBrain(tiny_blueprint())
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="Can we wrap this up?")
    llm = FakeLLM()
    register(llm, brain)

    await llm.registered[END_INTERVIEW](FakeParams(said="Can we wrap this up?", explicit=True))

    assert brain.withdrew is False
    assert "THEY HAVE ASKED TO STOP" in brain.plan_turn().system_instruction


async def test_a_soft_call_asks_once_instead_of_ending():
    """The model can say it is unsure. Then they get the courtesy of a question
    rather than having their interview ended on a guess."""
    brain = InterviewBrain(tiny_blueprint())
    llm = FakeLLM()
    register(llm, brain)

    params = FakeParams(said="I think I'm done", explicit=False)
    await llm.registered[END_INTERVIEW](params)

    assert brain.withdrew is False
    assert "THEY HAVE ASKED TO STOP" in brain.plan_turn().system_instruction


async def test_the_tool_returns_nothing_to_the_model():
    """The goodbye comes from the closing prompt, so it reads the same however
    the interview ended. Letting the model improvise one here would give two
    different farewells for the same event."""
    brain = InterviewBrain(tiny_blueprint())
    llm = FakeLLM()
    register(llm, brain)

    params = FakeParams(said="end the interview", explicit=True)
    await llm.registered[END_INTERVIEW](params)

    assert params.result is None


async def test_calling_it_twice_is_harmless():
    brain = InterviewBrain(tiny_blueprint())
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="Just end the interview.")
    llm = FakeLLM()
    register(llm, brain)

    for _ in range(3):
        await llm.registered[END_INTERVIEW](FakeParams(said="end the interview", explicit=True))

    assert brain.withdrew is True
    assert brain.current_section.kind == "closing"


# -- the two paths must agree ------------------------------------------------


def test_patterns_and_the_tool_share_one_entry_point():
    """Two ways in, one behaviour. Separate handling is how they drift."""
    by_pattern = InterviewBrain(tiny_blueprint())
    by_pattern.observe(bot_text="Tell me about a project.")
    by_pattern.observe(candidate_text="Just end the interview.")

    by_tool = InterviewBrain(tiny_blueprint())
    by_tool.observe(bot_text="Tell me about a project.")
    by_tool.candidate_asked_to_stop(said="Can we wrap this up?", explicit=True)

    assert by_pattern.withdrew == by_tool.withdrew is True
    assert by_pattern.current_section.id == by_tool.current_section.id


@pytest.mark.parametrize(
    "said",
    [
        "Can we wrap this up?",
        "This isn't for me, sorry",
        "I need to jump off",
        "Actually I'm no longer interested",
        "Bas, ab band karo",
    ],
)
def test_the_phrasings_patterns_will_never_catch(said):
    """Every one of these is missed by the pattern matcher, and every one is
    obvious to the model. This is the whole reason the tool exists."""
    from bot.brain.withdrawal import wants_to_stop

    assert wants_to_stop(said) is False, "if patterns now catch this, the test is stale"

    brain = InterviewBrain(tiny_blueprint())
    brain.candidate_asked_to_stop(said=said, explicit=True)
    assert brain.withdrew is True


# -- it must not fire on its own ---------------------------------------------


def test_a_brain_with_no_tool_call_carries_on():
    brain = InterviewBrain(tiny_blueprint())
    brain.observe(bot_text="Tell me about a project.")
    brain.observe(candidate_text="We had to end that project early because of budget.")

    assert brain.withdrew is False
    assert brain.current_section.kind != "closing"
