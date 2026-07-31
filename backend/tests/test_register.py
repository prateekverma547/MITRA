"""Field vocabulary, borrowed rather than invented.

A generic interview sounds like it was written by someone who has never done the
job. The fix is to speak the way the documents speak, and the safeguard is that
every term is checked against them: jargon used wrongly is far worse than plain
language, because a specialist hears one misused term and stops trusting the
whole interview.

Same shape as quote verification in the feedback scorer, and for the same
reason. A model asked for the vocabulary of a field will cheerfully supply
plausible terms nobody in this particular workplace uses.
"""

from blueprint.generate import MAX_VOCABULARY, build_register, verify_vocabulary

JD = """
We are hiring a Business Analyst for retail banking. You will run requirement
discovery sessions, produce BRDs and functional specifications, and drive
stakeholder sign-off across product and operations.
"""

CV = """
Business Analyst at Saxo Bank. Led requirement gathering for digital
self-service, authored user stories and BRDs, ran UAT with operations.
"""

SOURCE = JD + CV


# -- borrowed terms survive --------------------------------------------------


def test_terms_the_documents_use_are_kept():
    kept = verify_vocabulary(
        ["BRD", "requirement gathering", "stakeholder sign-off", "UAT", "user stories"],
        SOURCE,
    )

    assert kept == ["BRD", "requirement gathering", "stakeholder sign-off", "UAT", "user stories"]


def test_matching_ignores_case_and_punctuation():
    assert verify_vocabulary(["brd", "User Stories."], SOURCE) == ["brd", "User Stories."]


# -- invented terms do not ---------------------------------------------------


def test_plausible_jargon_the_documents_never_use_is_dropped():
    """The failure this exists to prevent: a bot confidently using a term from a
    neighbouring field, in front of someone who works in this one."""
    kept = verify_vocabulary(
        ["BRD", "story points", "OKRs", "sprint velocity", "RICE scoring"], SOURCE
    )

    assert kept == ["BRD"]


def test_a_partial_word_does_not_count_as_a_match():
    """"UA" must not match because "UAT" appears. Half a term is not a term."""
    assert verify_vocabulary(["UA", "requirement"], SOURCE) == ["requirement"]


def test_duplicates_are_collapsed():
    assert verify_vocabulary(["BRD", "brd", "B.R.D."], SOURCE) == ["BRD"]


def test_the_list_is_capped():
    """The live context is kept small deliberately, and an interviewer reciting
    thirty terms sounds like it is showing off rather than listening."""
    many = ["requirement"] * 3 + [f"BRD {i}" for i in range(40)]
    assert len(verify_vocabulary(["BRD"] * 1 + ["requirement gathering", "UAT"] + many, SOURCE)) <= MAX_VOCABULARY


def test_junk_input_is_survivable():
    assert verify_vocabulary(None, SOURCE) == []
    assert verify_vocabulary([None, 3, "", "   "], SOURCE) == []
    assert verify_vocabulary(["BRD"], "") == []


# -- assembling the register -------------------------------------------------


def test_a_register_is_built_from_verified_terms_only():
    register = build_register(
        {"domain_language": {"domain": "business analysis in retail banking",
                             "vocabulary": ["BRD", "sprint velocity"]}},
        SOURCE,
    )

    assert register.domain == "business analysis in retail banking"
    assert register.vocabulary == ["BRD"]


def test_no_register_when_there_is_nothing_to_say():
    """Empty is a fine answer. An interview with no field vocabulary just speaks
    plainly, which is never wrong."""
    assert build_register({}, SOURCE) is None
    assert build_register({"domain_language": {"domain": "", "vocabulary": ["OKRs"]}}, SOURCE) is None


# -- what the interviewer is told --------------------------------------------


def test_the_prompt_carries_the_borrowed_words():
    from bot.brain.brain import InterviewBrain
    from shared.contracts import InterviewRegister
    from tests.test_brain import tiny_blueprint

    blueprint = tiny_blueprint()
    blueprint.domain_language = InterviewRegister(
        domain="business analysis in retail banking",
        vocabulary=["BRD", "requirement gathering"],
    )

    instruction = InterviewBrain(blueprint).plan_turn().system_instruction

    assert "business analysis in retail banking" in instruction
    assert "BRD" in instruction
    # The caution matters as much as the words.
    assert "Plain language is always safe" in instruction


def test_a_blueprint_without_a_register_says_nothing_about_language():
    from bot.brain.brain import InterviewBrain
    from tests.test_brain import tiny_blueprint

    instruction = InterviewBrain(tiny_blueprint()).plan_turn().system_instruction

    assert "THE LANGUAGE OF THIS FIELD" not in instruction
